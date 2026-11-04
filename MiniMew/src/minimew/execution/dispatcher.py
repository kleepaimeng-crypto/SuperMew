"""应用拥有的 Run 驱动；任务集合仅管理资源，数据库裁决执行资格。"""

import asyncio
import logging
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError

from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor

logger = logging.getLogger(__name__)


class RunDispatcher:
    def __init__(
        self, repository, provider, *, scan_seconds=0.5, max_active=2, cancellation_poll=0.5
    ):
        self.repository = repository
        self.executor = (
            RunExecutor(repository, provider, cancellation_poll=cancellation_poll)
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
            logger.error("execution_task_failed run_id=%s code=EXECUTION_TASK_ERROR", run)

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
    provider = None
    if settings.model_credentials_available:
        from minimew.execution.deepseek import DeepSeekProvider

        provider = DeepSeekProvider(settings.deepseek_api_key)
    else:
        logger.warning("execution_disabled code=MODEL_CONFIGURATION_UNAVAILABLE")
    dispatcher = RunDispatcher(
        ExecutionRepository(sessions),
        provider,
        scan_seconds=settings.execution_scan_seconds,
        max_active=settings.execution_max_active,
        cancellation_poll=settings.cancellation_poll_seconds,
    )
    task = asyncio.create_task(dispatcher.run(), name="minimew-run-dispatcher")

    def report_failure(done):
        if not done.cancelled() and done.exception() is not None:
            logger.error("execution_dispatcher_stopped code=UNEXPECTED_DISPATCHER_ERROR")

    task.add_done_callback(report_failure)
    try:
        yield dispatcher
    finally:
        task.cancel()  # 先停止扫描和领取，再等待本实例的执行任务。
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
        finally:
            await dispatcher.drain()
