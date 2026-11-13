"""领取 → 共用绝对截止时间的有界尝试 → 原子结果提交。"""

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from minimew.evidence import ANSWER_INSTRUCTIONS, AnswerResult, parse_answer
from minimew.execution.context import ContextInvalid
from minimew.execution.messages import (
    MAX_OUTPUT_CHARS,
    InputMessage,
    ModelTurn,
    valid_text,
    validate_turn,
)
from minimew.execution.provider import (
    MAX_DELTA_CHARS,
    Provider,
    ProviderCompleted,
    ProviderDelta,
    ProviderEvent,
    ProviderFailure,
    ProviderRequest,
)
from minimew.execution.repository import (
    CancellationRequested,
    Claim,
    ExecutionRepository,
    OwnershipLost,
)
from minimew.execution.tools import ToolContext, ToolFailure, ToolRegistry
from minimew.generated.run_event_v1 import ErrorCode
from minimew.model_control import ModelSnapshotUnavailable

ExecutionOutcome = Literal["succeeded", "failed", "cancelled", "not_claimed", "ownership_lost"]
logger = logging.getLogger(__name__)


class ExecutionStorageUnavailable(Exception):
    pass


@dataclass
class AttemptProgress:
    body_committed: bool = False


@dataclass
class RunBudget:
    model_requests: int = 0
    tool_calls: int = 0

    def require_model(self):
        if self.model_requests >= 4:
            raise ToolFailure("AGENT_BUDGET_EXCEEDED")

    def require_tools(self, count):
        self.require_model()  # 工具执行后必须仍有机会向模型交回结果。
        if self.tool_calls + count > 6:
            raise ToolFailure("AGENT_BUDGET_EXCEEDED")


class RunExecutor:
    def __init__(
        self,
        repository: ExecutionRepository,
        provider: Provider,
        *,
        cancellation_poll=0.5,
        registry: ToolRegistry | None = None,
        capabilities: frozenset[str] = frozenset(),
    ):
        self.repository = repository
        self.provider = provider
        self.cancellation_poll = cancellation_poll
        self.registry = registry if registry is not None else ToolRegistry()
        self.capabilities = capabilities

    async def execute(self, user_id: UUID, thread_id: UUID, run_id: UUID) -> ExecutionOutcome:
        claim = await self.repository.claim(user_id, thread_id, run_id)
        if claim is None:
            return "not_claimed"
        error: ErrorCode | None = None
        content = ""
        materials = None
        try:
            request = await self.repository.request(claim)
            context = self.registry.bind_context(
                ToolContext(
                    user_id,
                    thread_id,
                    run_id,
                    self.capabilities,
                    **({"rag_policy": request.snapshot.rag} if request.snapshot.rag else {}),
                )
            )
            tools = self.registry.visible(context)
            if tools:
                try:
                    request = replace(request, tools=tools)
                except ValueError:
                    raise ContextInvalid from None
            await self.repository.check_control(claim)
            remaining = await self.repository.remaining(claim)
            if remaining <= 0:
                await self.repository.expire(claim)
                return "ownership_lost"
            loop = asyncio.get_running_loop()
            deadline = loop.time() + min(
                remaining, request.snapshot.answer.configuration.timeout_seconds
            )
            answer = await self._watched_generate(claim, request, deadline, context)
            content, materials = answer.content, answer.materials
        except ModelSnapshotUnavailable as failure:
            error = failure.code
        except ContextInvalid:
            error = "CONTEXT_INVALID"
        except ProviderFailure as failure:
            error = failure.code
        except ToolFailure as failure:
            error = failure.code
        except CancellationRequested:
            # 统一终结事务会重新检查持久取消标记，并清除非失败终态的 error_code。
            error = "EXECUTOR_INTERRUPTED"
        except ExecutionStorageUnavailable:
            logger.warning("execution_monitor_failed code=EXECUTION_STORAGE_UNAVAILABLE")
            async with asyncio.timeout(3):
                return await self._finish(claim, "", "EXECUTION_STORAGE_UNAVAILABLE")
        except OwnershipLost:
            await self.repository.expire(claim)
            return "ownership_lost"
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(3):
                    await self.repository.finish(claim, error_code="EXECUTOR_INTERRUPTED")
            except (SQLAlchemyError, OSError, TimeoutError):
                logger.warning("execution_interrupt_save_failed code=EXECUTION_STORAGE_UNAVAILABLE")
            raise
        return await self._finish(claim, content, error, materials=materials)

    async def _finish(self, claim, content, error, *, materials=None):
        extra = {"materials": materials} if materials is not None else {}
        saved = await self.repository.finish(claim, content=content, error_code=error, **extra)
        if not saved:
            await self.repository.expire(claim)
            return "ownership_lost"
        return saved

    async def _watch_control(self, claim):
        while True:
            # 调用前已检查过资格，之后按间隔查询，避免重复首轮查询占用模型启动预算。
            await asyncio.sleep(self.cancellation_poll)
            try:
                await self.repository.check_control(claim)
            except (SQLAlchemyError, TimeoutError, OSError):
                raise ExecutionStorageUnavailable from None

    async def _watched_generate(self, claim, request, deadline, context):
        model = asyncio.create_task(
            self._generate(claim, request, deadline, context),
            name=f"minimew-model-{request.run_id}",
        )
        monitor = asyncio.create_task(
            self._watch_control(claim),
            name=f"minimew-cancel-monitor-{request.run_id}",
        )
        tasks = {model, monitor}
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )
            if not done:
                if request.tools:
                    raise ToolFailure("AGENT_DEADLINE_EXCEEDED")
                raise ProviderFailure("PROVIDER_TIMEOUT")
            if monitor in done:
                await monitor  # 监视异常保留其取消/存储/资格语义，不转换为 Provider 错误。
            return await model
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            done, pending = await asyncio.wait(tasks, timeout=3)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                logger.warning("execution_cleanup_timeout count=%s", len(pending))
                for task in pending:
                    # 首次取消可能已被 Provider 吞掉；清理预算结束后再次请求停止。
                    task.cancel()
                    task.add_done_callback(self._collect_task)
                await asyncio.sleep(0)

    @staticmethod
    def _collect_task(task):
        if not task.cancelled():
            task.exception()

    async def _check_active(self, claim, request, deadline):
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        try:
            await self.repository.check_control(claim)
        except (SQLAlchemyError, TimeoutError, OSError):
            raise ExecutionStorageUnavailable from None
        if asyncio.get_running_loop().time() >= deadline:
            if request.tools:
                raise ToolFailure("AGENT_DEADLINE_EXCEEDED")
            raise ProviderFailure("PROVIDER_TIMEOUT")

    async def _generate(self, claim, request, deadline, context) -> AnswerResult:
        budget = RunBudget()
        progress = AttemptProgress()
        while True:
            turn = await self._model_turn(claim, request, deadline, progress, budget)
            if turn.kind == "text":
                if context.evidence.queried:
                    try:
                        return parse_answer(turn.content, context.evidence.items)
                    except ValueError:
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE") from None
                return AnswerResult(turn.content)
            await self._check_active(claim, request, deadline)
            assistant = InputMessage("assistant", turn.content, turn.tool_calls)
            prepared = self.registry.prepare(turn.tool_calls, context)
            # 执行前确认原调用ID未重复，且调用数据本身可装入下一轮上下文。
            placeholders = tuple(
                InputMessage("tool", "{}", tool_call_id=call.id) for call in turn.tool_calls
            )
            self._with_messages(request, (*request.messages, assistant, *placeholders))
            budget.require_tools(len(prepared))
            results = []
            for call in prepared:
                await self._check_active(claim, request, deadline)
                budget.tool_calls += 1
                observation = {"tool_call_id": call.call.id, "tool_name": call.call.name}
                await self._record_tool(claim, request, deadline, "tool.started", observation)
                try:
                    async with asyncio.timeout_at(deadline) as timeout:
                        content = await self.registry.invoke(call, context)
                except TimeoutError:
                    raise ToolFailure("AGENT_DEADLINE_EXCEEDED") from None
                except ToolFailure as failure:
                    await self._record_tool(
                        claim,
                        request,
                        deadline,
                        "tool.failed",
                        {**observation, "error_code": failure.code},
                    )
                    raise
                if timeout.expired():
                    raise ToolFailure("AGENT_DEADLINE_EXCEEDED")
                await self._check_active(claim, request, deadline)
                await self._record_tool(claim, request, deadline, "tool.completed", observation)
                results.append(InputMessage("tool", content, tool_call_id=call.call.id))
            try:
                request = replace(
                    request,
                    messages=(*request.messages, assistant, *results),
                    instructions=ANSWER_INSTRUCTIONS
                    if context.evidence.queried
                    else request.instructions,
                )
            except ValueError:
                raise ContextInvalid from None

    async def _record_tool(self, claim, request, deadline, event_type, data):
        for attempt in range(2):
            await self._check_active(claim, request, deadline)
            try:
                if not await self.repository.record_tool(claim, event_type, data):
                    raise OwnershipLost
                return
            except (SQLAlchemyError, OSError, TimeoutError):
                if attempt or asyncio.get_running_loop().time() + 0.1 >= deadline:
                    raise ExecutionStorageUnavailable from None
                await asyncio.sleep(0.1)

    @staticmethod
    def _with_messages(request, messages):
        try:
            return replace(request, messages=messages)
        except ValueError:
            raise ContextInvalid from None

    async def _model_turn(self, claim, request, deadline, progress, budget) -> ModelTurn:
        for attempt in range(2):
            if attempt:
                await self._check_active(claim, request, deadline)
            budget.require_model()
            budget.model_requests += 1
            try:
                return await self._consume(claim, request, deadline, progress)
            except ProviderFailure as failure:
                if request.tools and asyncio.get_running_loop().time() >= deadline:
                    raise ToolFailure("AGENT_DEADLINE_EXCEEDED") from None
                if attempt == 1 or not failure.retryable or progress.body_committed:
                    raise
                budget.require_model()
                if await self.repository.remaining(claim) <= 0:
                    raise OwnershipLost from None
                delay = max(0.5, failure.retry_after)
                if asyncio.get_running_loop().time() + delay >= deadline:
                    raise ProviderFailure("PROVIDER_TIMEOUT") from None
                try:
                    async with asyncio.timeout_at(deadline):
                        await asyncio.sleep(delay)
                except TimeoutError:
                    raise ProviderFailure("PROVIDER_TIMEOUT") from None
                if await self.repository.remaining(claim) <= 0:
                    raise OwnershipLost from None
        raise AssertionError("Unreachable retry state")

    async def _consume(
        self, claim: Claim, request: ProviderRequest, deadline: float, progress: AttemptProgress
    ) -> ModelTurn:
        try:
            stream = self.provider.stream(request)
        except ProviderFailure:
            raise
        except Exception:
            raise ProviderFailure() from None
        completed: ProviderCompleted | None = None
        total = 0
        try:
            while (event := await self._next(stream, deadline)) is not None:
                if completed is not None:
                    raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                if isinstance(event, ProviderCompleted):
                    try:
                        validate_turn(event.turn)
                    except ValueError:
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE") from None
                    if event.turn.kind != "text" and not request.tools:
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                    completed = event
                else:
                    # 工具模式禁止中间文字/参数直接进入公开Journal。
                    if request.tools:
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                    if not valid_text(event.content, MAX_DELTA_CHARS):
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                    total += len(event.content)
                    if total > MAX_OUTPUT_CHARS:
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                    # 数据库异常不进入 Provider 的异常归一化边界。
                    if not await self.repository.append_delta(claim, event.content):
                        raise OwnershipLost
                    progress.body_committed = True
        finally:
            primary_error = sys.exception()
            try:
                async with asyncio.timeout(3):
                    await stream.aclose()
            except Exception:
                if primary_error is None:
                    raise ProviderFailure() from None
                # 清理错误不能覆盖数据库故障或原有失败；不附加原始 Provider 异常。
                primary_error.add_note("Provider stream cleanup also failed")
        if completed is None:
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        return completed.turn

    @staticmethod
    async def _next(
        stream: AsyncGenerator[ProviderEvent, None], deadline: float
    ) -> ProviderEvent | None:
        loop = asyncio.get_running_loop()
        if loop.time() >= deadline:
            raise ProviderFailure("PROVIDER_TIMEOUT")
        try:
            async with asyncio.timeout_at(deadline) as timeout:
                event = await anext(stream)
        except StopAsyncIteration:
            if loop.time() >= deadline:
                raise ProviderFailure("PROVIDER_TIMEOUT") from None
            return None
        except TimeoutError:
            raise ProviderFailure("PROVIDER_TIMEOUT") from None
        except ProviderFailure:
            raise
        except Exception:
            raise ProviderFailure() from None
        # 不信任吞掉 CancelledError 后迟到输出的 Provider。
        if timeout.expired() or loop.time() >= deadline:
            raise ProviderFailure("PROVIDER_TIMEOUT")
        if not isinstance(event, (ProviderDelta, ProviderCompleted)):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        return event
