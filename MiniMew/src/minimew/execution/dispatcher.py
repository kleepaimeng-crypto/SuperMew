"""应用拥有的 Run 驱动；任务集合仅管理资源，数据库裁决执行资格。"""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError

from minimew.execution.diagnostics import capture, emit
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor

logger = logging.getLogger(__name__)


class RunDispatcher:
    def __init__(
        self,
        repository,
        provider,
        *,
        scan_seconds=0.5,
        max_active=2,
        cancellation_poll=0.5,
        heartbeat_seconds=5.0,
        registry=None,
        capabilities=frozenset(),
    ):
        self.repository = repository
        self.executor = (
            RunExecutor(
                repository,
                provider,
                cancellation_poll=cancellation_poll,
                heartbeat_seconds=heartbeat_seconds,
                registry=registry,
                capabilities=capabilities,
            )
            if provider is not None
            else None
        )
        self.scan_seconds = scan_seconds
        self.max_active = max_active
        self.active = {}

    async def run(self):
        next_recovery = 0
        while True:
            try:
                if asyncio.get_running_loop().time() >= next_recovery:
                    async with asyncio.timeout(10):
                        await self.repository.recover_expired(limit=100)
                    next_recovery = asyncio.get_running_loop().time() + 5
                free = self.max_active - len(self.active)
                if self.executor is not None and free > 0:
                    async with asyncio.timeout(5):
                        candidates = await self.repository.candidates(limit=free)
                    for user, thread, run in candidates:
                        if run not in self.active:
                            task = asyncio.create_task(
                                self.executor.execute(user, thread, run), name=f"minimew-run-{run}"
                            )
                            self.active[run] = task
                            task.add_done_callback(lambda done, run=run: self._finished(run, done))
            except (SQLAlchemyError, TimeoutError, OSError):
                logger.warning("execution_scan_retry code=EXECUTION_STORAGE_UNAVAILABLE")
            await asyncio.sleep(self.scan_seconds)

    def _finished(self, run, task):
        self.active.pop(run, None)
        if not task.cancelled() and task.exception() is not None:
            # 记录稳定诊断，不把 SDK/数据库原始异常或凭据写入日志。
            emit(
                logger,
                "execution_task_failed",
                run,
                "EXECUTION_TASK_ERROR",
                capture("executor_task", "unexpected_task_failure", task.exception()),
            )

    async def drain(self):
        tasks = set(self.active.values())
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=5)
        for task in pending:
            task.cancel()
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=7)
            if unfinished:
                logger.warning("execution_shutdown_timeout count=%s", len(unfinished))


@asynccontextmanager
async def execution_lifespan(sessions, settings):
    from minimew.account_models.credentials import CredentialResolver
    from minimew.execution.chat_provider import ChatProvider
    from minimew.execution.tools import ToolRegistry
    from minimew.knowledge import CAPABILITY
    from minimew.knowledge_runtime import knowledge_lifespan

    # 账户凭据可独立于部署默认Key工作；可用性在新Run解析与每次调用时核验。
    provider = ChatProvider(
        settings.deepseek_api_key, credentials=CredentialResolver(sessions, settings)
    )
    async with AsyncExitStack() as stack:
        knowledge = await stack.enter_async_context(knowledge_lifespan(sessions, settings))
        dispatcher = RunDispatcher(
            ExecutionRepository(sessions, lease_seconds=settings.execution_lease_seconds),
            provider,
            scan_seconds=settings.execution_scan_seconds,
            max_active=settings.execution_max_active,
            cancellation_poll=settings.cancellation_poll_seconds,
            heartbeat_seconds=settings.execution_heartbeat_seconds,
            registry=ToolRegistry((knowledge.spec(),)) if knowledge is not None else None,
            capabilities=frozenset({CAPABILITY}) if knowledge is not None else frozenset(),
        )
        task = asyncio.create_task(dispatcher.run(), name="minimew-run-dispatcher")

        def report_failure(done):
            if not done.cancelled() and done.exception() is not None:
                logger.error("execution_dispatcher_stopped code=UNEXPECTED_DISPATCHER_ERROR")

        task.add_done_callback(report_failure)
        try:
            yield dispatcher
        finally:
            task.cancel()  # 先停止扫描/执行，再由ExitStack关闭知识资源。
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
            finally:
                await dispatcher.drain()
