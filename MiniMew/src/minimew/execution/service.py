"""领取 → 共用绝对截止时间的有界尝试 → 原子结果提交。"""

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from minimew.evidence import AnswerResult
from minimew.execution.budget import ExecutionBudget as RunBudget
from minimew.execution.context import ContextInvalid
from minimew.execution.control import ExecutionStorageUnavailable
from minimew.execution.diagnostics import capture, emit
from minimew.execution.grounded import direct_answer, grounded_answer
from minimew.execution.messages import (
    MAX_OUTPUT_CHARS,
    InputMessage,
    ModelTurn,
    valid_text,
    validate_turn,
)
from minimew.execution.process import ProcessReporter, report_step
from minimew.execution.prompts import chat_instructions
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
from minimew.hitl.policy import CheckpointExpired, PauseRequested
from minimew.hitl.store import HitlStore
from minimew.model_control import ModelSnapshotUnavailable

ExecutionOutcome = Literal[
    "succeeded", "failed", "cancelled", "not_claimed", "ownership_lost", "waiting_input"
]
logger = logging.getLogger(__name__)


@dataclass
class AttemptProgress:
    body_committed: bool = False


class RunExecutor:
    def __init__(
        self,
        repository: ExecutionRepository,
        provider: Provider,
        *,
        cancellation_poll=0.5,
        heartbeat_seconds=5.0,
        registry: ToolRegistry | None = None,
        capabilities: frozenset[str] = frozenset(),
    ):
        self.repository = repository
        self.provider = provider
        self.cancellation_poll = cancellation_poll
        if heartbeat_seconds <= 0:
            raise ValueError("Heartbeat interval must be positive")
        self.heartbeat_seconds = heartbeat_seconds
        self.registry = registry if registry is not None else ToolRegistry()
        self.capabilities = capabilities

    async def execute(self, user_id: UUID, thread_id: UUID, run_id: UUID) -> ExecutionOutcome:
        claim = await self.repository.claim(user_id, thread_id, run_id)
        if claim is None:
            return "not_claimed"
        error: ErrorCode | None = None
        content = ""
        materials = None
        diagnostic = None
        failed_tool = None
        try:
            request = await self.repository.request(claim)
            context = self.registry.bind_context(
                ToolContext(
                    user_id,
                    thread_id,
                    run_id,
                    self.capabilities,
                    **({"rag_policy": request.snapshot.rag} if request.snapshot.rag else {}),
                    reasoning=request.snapshot.reasoning,
                    repairs=request.snapshot.repairs,
                    hitl=request.snapshot.hitl,
                    question=request.messages[-1].content,
                )
            )
            tools = self.registry.visible(context)
            if tools:
                try:
                    request = replace(request, tools=tools)
                except ValueError:
                    raise ContextInvalid from None
            await self.repository.check_control(claim)
            remaining = request.snapshot.answer.configuration.timeout_seconds
            loop = asyncio.get_running_loop()
            saved = None
            if request.snapshot.hitl is not None and claim.resumed:
                try:
                    saved = await HitlStore(self.repository).load(claim, request.snapshot)
                    if saved is None:
                        raise ValueError("Missing resume checkpoint")
                except CheckpointExpired:
                    raise ToolFailure("HITL_EXPIRED") from None
                except (ValueError, KeyError, TypeError):
                    raise ToolFailure("HITL_CHECKPOINT_INVALID") from None
                from minimew.knowledge import NAME

                if NAME not in context.tool_sessions:
                    raise ToolFailure("TOOL_NOT_AVAILABLE")
                context.tool_sessions[NAME].scope = saved["scope"]
                context.evidence.items = saved["evidence"]
                context.evidence.queried = saved["queried"]
                remaining = min(remaining, saved["remaining"])
            # 用查询开始前的 monotonic 时间换算，数据库往返不会额外发放执行时间。
            began = loop.time()
            remaining = await self.repository.activate(claim, remaining)
            deadline = began + remaining
            context = replace(context, deadline=deadline, checkpoint=saved)
            try:
                answer = await self._watched_generate(claim, request, deadline, context)
            except PauseRequested as pause:
                try:
                    await HitlStore(self.repository).pause(
                        claim,
                        pause.request,
                        pause.context,
                        pause,
                        pause.outer,
                        pause.budget,
                        deadline,
                    )
                except (ValueError, KeyError, TypeError):
                    raise ToolFailure("HITL_CHECKPOINT_INVALID") from None
                return "waiting_input"
            content, materials = answer.content, answer.materials
        except ModelSnapshotUnavailable as failure:
            error = failure.code
            diagnostic = capture("snapshot", "snapshot_unavailable", failure)
        except ContextInvalid:
            error = "CONTEXT_INVALID"
            diagnostic = capture("context", "context_invalid")
        except ProviderFailure as failure:
            error = failure.code
            diagnostic = failure.diagnostic or capture("answer_model", "provider_failed", failure)
            failed_tool = getattr(failure, "tool_call_id", None)
        except ToolFailure as failure:
            error = failure.code
            diagnostic = failure.diagnostic or capture("tool", "tool_failed", failure)
            failed_tool = getattr(failure, "tool_call_id", None)
        except CancellationRequested:
            # 统一终结事务会重新检查持久取消标记，并清除非失败终态的 error_code。
            error = "EXECUTOR_INTERRUPTED"
        except ExecutionStorageUnavailable as failure:
            emit(
                logger,
                "execution_storage_unavailable",
                run_id,
                "EXECUTION_STORAGE_UNAVAILABLE",
                capture("execution_control", "storage_unavailable", failure),
                thread_id=thread_id,
            )
            async with asyncio.timeout(3):
                return await self._finish(
                    claim, "", "EXECUTION_STORAGE_UNAVAILABLE", run_id=run_id, thread_id=thread_id
                )
        except OwnershipLost:
            await self.repository.expire(claim)
            return "ownership_lost"
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(3):
                    await self.repository.finish(claim, error_code="EXECUTOR_INTERRUPTED")
            except (SQLAlchemyError, OSError, TimeoutError) as failure:
                emit(
                    logger,
                    "execution_interrupt_save_failed",
                    run_id,
                    "EXECUTION_STORAGE_UNAVAILABLE",
                    capture("interrupt_write", "storage_write_failed", failure),
                    thread_id=thread_id,
                )
            raise
        if error is not None and diagnostic is not None:
            # 表示失败已被执行器处理；持久终态仍由下面事务裁定（取消可能先获胜）。
            emit(
                logger,
                "execution_failure_handled",
                run_id,
                error,
                diagnostic,
                thread_id=thread_id,
                tool_call_id=failed_tool,
            )
        return await self._finish(
            claim, content, error, materials=materials, run_id=run_id, thread_id=thread_id
        )

    async def _finish(self, claim, content, error, *, materials=None, run_id, thread_id):
        extra = {"materials": materials} if materials is not None else {}
        try:
            saved = await self.repository.finish(claim, content=content, error_code=error, **extra)
        except (SQLAlchemyError, OSError, TimeoutError) as failure:
            emit(
                logger,
                "execution_terminal_write_failed",
                run_id,
                "EXECUTION_STORAGE_UNAVAILABLE",
                capture("terminal_write", "storage_write_failed", failure),
                thread_id=thread_id,
            )
            raise
        if not saved:
            await self.repository.expire(claim)
            return "ownership_lost"
        return saved

    async def _watch_control(self, claim, deadline):
        next_heartbeat = asyncio.get_running_loop().time() + self.heartbeat_seconds
        while True:
            # 调用前已检查过资格，之后按间隔查询，避免重复首轮查询占用模型启动预算。
            await asyncio.sleep(min(self.cancellation_poll, self.heartbeat_seconds))
            try:
                await self.repository.check_control(claim)
                now = asyncio.get_running_loop().time()
                if now < deadline and now >= next_heartbeat:
                    await self.repository.heartbeat(claim)
                    next_heartbeat = asyncio.get_running_loop().time() + self.heartbeat_seconds
            except (SQLAlchemyError, TimeoutError, OSError):
                raise ExecutionStorageUnavailable from None

    async def _watched_generate(self, claim, request, deadline, context):
        model = asyncio.create_task(
            self._generate(claim, request, deadline, context),
            name=f"minimew-model-{request.run_id}",
        )
        monitor = asyncio.create_task(
            self._watch_control(claim, deadline),
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
                logger.warning(
                    "execution_cleanup_timeout run_id=%s count=%s", request.run_id, len(pending)
                )
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
        resume = context.checkpoint
        budget = (
            RunBudget(**resume["budget"])
            if resume
            else RunBudget(
                reasoning=context.reasoning is not None,
                finalization=getattr(context.repairs, "finalization", None) == "reserved-v1",
            )
        )
        if resume:
            request = replace(
                request,
                messages=resume["outer"]["messages"],
                instructions=resume["outer"]["instructions"],
            )
        context = replace(
            context,
            process=ProcessReporter(
                request.run_id,
                lambda data: self._record_process(claim, request, deadline, data),
                lambda data: self._record_process(claim, request, deadline, data, detail=True),
            ),
        )
        request = self._with_instructions(request, context)
        if context.reasoning is not None:
            from minimew.execution.reasoning import RunModelCaller

            caller = RunModelCaller(
                self.provider,
                request,
                budget,
                deadline,
                lambda: self._check_active(claim, request, deadline),
                context.reasoning,
            )
            context = replace(
                context,
                model_caller=caller,
                run_budget=budget,
                question=context.question or request.messages[-1].content,
            )
        progress = AttemptProgress(resume["outer"]["body_committed"] if resume else False)
        while True:
            if resume:
                previous = resume["outer"]["assistant"]
                turn = ModelTurn("tool_calls", previous.content, previous.tool_calls)
            else:
                if budget.finalization and request.tools and not budget.can_request_tools():
                    request = replace(
                        request,
                        force_answer=True,
                        instructions=chat_instructions(
                            context,
                            request.tools,
                            project=request.project_context,
                            force_answer=True,
                        ),
                    )
                turn = await self._model_turn(
                    claim, request, deadline, progress, budget, context.process
                )
            if turn.kind == "text":
                if context.evidence.queried:
                    return await self._grounded_answer(
                        claim, request, turn, context, deadline, progress, budget
                    )
                return direct_answer(turn.content)
            await self._check_active(claim, request, deadline)
            if request.force_answer:
                raise ProviderFailure(
                    "PROVIDER_INVALID_RESPONSE",
                    diagnostic=capture("answer_model", "final_answer_requested_tools"),
                )
            assistant = InputMessage("assistant", turn.content, turn.tool_calls)
            prepared = self.registry.prepare(turn.tool_calls, context)
            # 执行前确认原调用ID未重复，且调用数据本身可装入下一轮上下文。
            placeholders = tuple(
                InputMessage("tool", "{}", tool_call_id=call.id) for call in turn.tool_calls
            )
            self._with_messages(request, (*request.messages, assistant, *placeholders))
            if not resume:
                budget.require_tools(len(prepared))
            results = list(resume["outer"]["results"]) if resume else []
            for index, call in enumerate(prepared):
                if index < len(results):
                    continue
                await self._check_active(claim, request, deadline)
                observation = {"tool_call_id": call.call.id, "tool_name": call.call.name}
                if not resume:
                    budget.tool_calls += 1
                    await self._record_tool(claim, request, deadline, "tool.started", observation)
                try:
                    async with asyncio.timeout_at(deadline) as timeout:
                        content = await self.registry.invoke(
                            call,
                            replace(
                                context,
                                process=context.process.tool(call.call.id)
                                if context.process
                                else None,
                            ),
                        )
                except PauseRequested as pause:
                    pause.outer = {
                        "messages": request.messages,
                        "instructions": request.instructions,
                        "assistant": assistant,
                        "results": tuple(results),
                        "body_committed": progress.body_committed,
                    }
                    pause.context, pause.request, pause.budget = context, request, budget
                    raise
                except TimeoutError:
                    raise ToolFailure("AGENT_DEADLINE_EXCEEDED") from None
                except (ToolFailure, ProviderFailure) as failure:
                    failure.tool_call_id = call.call.id
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
                if resume:
                    resume = None
                    context = replace(context, checkpoint=None)
            try:
                request = replace(
                    request,
                    messages=(*request.messages, assistant, *results),
                    instructions=chat_instructions(
                        context, request.tools, project=request.project_context
                    ),
                )
            except ValueError:
                raise ContextInvalid from None

    async def _grounded_answer(self, claim, request, turn, context, deadline, progress, budget):
        return await grounded_answer(
            request,
            turn,
            context,
            budget,
            lambda: self._check_active(claim, request, deadline),
            lambda repair: self._model_turn(
                claim, repair, deadline, progress, budget, context.process, purpose="answer_repair"
            ),
        )

    async def _record_process(self, claim, request, deadline, data, *, detail=False):
        for attempt in range(2):
            await self._check_active(claim, request, deadline)
            try:
                if not await self.repository.record_process(
                    claim, data, **({"detail": True} if detail else {})
                ):
                    raise OwnershipLost
                return
            except (SQLAlchemyError, OSError, TimeoutError):
                if attempt or asyncio.get_running_loop().time() + 0.1 >= deadline:
                    raise ExecutionStorageUnavailable from None
                await asyncio.sleep(0.1)

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
    def _with_instructions(request, context):
        try:
            return replace(
                request,
                instructions=chat_instructions(
                    context, request.tools, project=request.project_context
                ),
            )
        except ValueError:
            raise ContextInvalid from None

    @staticmethod
    def _with_messages(request, messages):
        try:
            return replace(request, messages=messages)
        except ValueError:
            raise ContextInvalid from None

    async def _model_turn(
        self, claim, request, deadline, progress, budget, process=None, *, purpose="model_decision"
    ) -> ModelTurn:
        for attempt in range(2):
            if attempt:
                await self._check_active(claim, request, deadline)
            budget.require_model()
            budget.model_requests += 1
            try:
                async with report_step(
                    process,
                    "model",
                    f"model/{budget.model_requests}",
                    purpose=purpose,
                    attempt=attempt + 1,
                ) as step:
                    turn = await self._consume(claim, request, deadline, progress)
                    if step:
                        step.finish(
                            summary="模型请求结束",
                            count=1,
                            outcome=turn.kind,
                            purpose="answer_repair"
                            if purpose == "answer_repair"
                            else "tool_selection"
                            if turn.kind == "tool_calls"
                            else "answer",
                        )
                    return turn
            except ProviderFailure as failure:
                if (
                    request.tools or request.private_output
                ) and asyncio.get_running_loop().time() >= deadline:
                    raise ToolFailure("AGENT_DEADLINE_EXCEEDED") from None
                if attempt == 1 or not failure.retryable or progress.body_committed:
                    raise
                budget.require_model()
                await self._check_active(claim, request, deadline)
                delay = max(0.5, failure.retry_after)
                if asyncio.get_running_loop().time() + delay >= deadline:
                    raise ProviderFailure("PROVIDER_TIMEOUT") from None
                emit(
                    logger,
                    "model_retry",
                    request.run_id,
                    failure.code,
                    failure.diagnostic
                    or capture("answer_model", "retryable_provider_failure", failure),
                    thread_id=request.thread_id,
                    attempt=attempt + 1,
                )
                try:
                    async with asyncio.timeout_at(deadline):
                        await asyncio.sleep(delay)
                except TimeoutError:
                    raise ProviderFailure("PROVIDER_TIMEOUT") from None
                await self._check_active(claim, request, deadline)
        raise AssertionError("Unreachable retry state")

    async def _consume(
        self, claim: Claim, request: ProviderRequest, deadline: float, progress: AttemptProgress
    ) -> ModelTurn:
        try:
            stream = self.provider.stream(request)
        except ProviderFailure:
            raise
        except Exception as failure:
            raise ProviderFailure(
                diagnostic=capture(
                    "answer_model",
                    "stream_initialization_failed",
                    failure,
                )
            ) from None
        completed: ProviderCompleted | None = None
        total = 0
        try:
            while (event := await self._next(stream, deadline)) is not None:
                if completed is not None:
                    raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                if isinstance(event, ProviderCompleted):
                    try:
                        validate_turn(event.turn)
                    except ValueError as failure:
                        raise ProviderFailure(
                            "PROVIDER_INVALID_RESPONSE",
                            diagnostic=capture(
                                "answer_model",
                                "model_turn_invalid",
                                failure,
                            ),
                        ) from None
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
                    if not request.private_output:
                        if not await self.repository.append_delta(claim, event.content):
                            raise OwnershipLost
                        progress.body_committed = True
        finally:
            primary_error = sys.exception()
            try:
                async with asyncio.timeout(3):
                    await stream.aclose()
            except Exception as failure:
                if primary_error is None:
                    raise ProviderFailure(
                        diagnostic=capture(
                            "answer_model",
                            "stream_cleanup_failed",
                            failure,
                        )
                    ) from None
                emit(
                    logger,
                    "model_cleanup_failed",
                    request.run_id,
                    "PROVIDER_ERROR",
                    capture("answer_model", "stream_cleanup_failed", failure),
                    thread_id=request.thread_id,
                )
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
        except Exception as failure:
            raise ProviderFailure(
                diagnostic=capture(
                    "answer_model",
                    "provider_stream_failed",
                    failure,
                )
            ) from None
        # 不信任吞掉 CancelledError 后迟到输出的 Provider。
        if timeout.expired() or loop.time() >= deadline:
            raise ProviderFailure("PROVIDER_TIMEOUT")
        if not isinstance(event, (ProviderDelta, ProviderCompleted)):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        return event
