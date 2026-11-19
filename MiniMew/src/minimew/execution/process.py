"""Run私有公开过程报告；宿主保存事实，业务模块不持有数据库或浏览器。"""

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class ProcessReporter:
    run_id: object
    sink: object
    detail_sink: object | None = None
    path: str = ""
    parent_id: str = ""
    tool_call_id: str = ""

    def tool(self, call_id):
        return replace(self, path="tool/" + call_id, parent_id="", tool_call_id=call_id)

    @asynccontextmanager
    async def step(self, kind, key, *, query="", phase="", purpose="", attempt=0):
        identity = uuid5(NAMESPACE_URL, f"{self.run_id}/{self.path}/{key}").hex
        data = {
            "step_id": identity,
            "parent_step_id": self.parent_id,
            "tool_call_id": self.tool_call_id,
            "kind": kind,
            "status": "running",
            "query": query,
            "summary": "",
            "count": 0,
            "sources": [],
            "outcome": "",
            "elapsed_ms": 0,
        }
        await self.sink(data)
        step = ProcessStep(replace(self, path=self.path + "/" + key, parent_id=identity), data)
        step.detail = dict(
            step_id=identity,
            phase=phase,
            purpose=purpose,
            attempt=attempt,
            missing=[],
            questions=[],
            sources=[],
        )
        if self.detail_sink and (phase or purpose or attempt):
            await self.detail_sink({**step.detail, "stage": "start"})
        started = time.monotonic()
        try:
            yield step
        except Exception as error:
            from minimew.execution.control import ExecutionStorageUnavailable
            from minimew.execution.repository import CancellationRequested, OwnershipLost
            from minimew.hitl.policy import PauseRequested

            if isinstance(
                error,
                (
                    ExecutionStorageUnavailable,
                    CancellationRequested,
                    OwnershipLost,
                    PauseRequested,
                    TimeoutError,
                ),
            ):
                raise
            await self.sink(
                {
                    **data,
                    "status": "failed",
                    "outcome": getattr(error, "code", "PROCESS_STAGE_FAILED"),
                    "elapsed_ms": min(86400000, int((time.monotonic() - started) * 1000)),
                }
            )
            raise
        else:
            if self.detail_sink and (step.detail_changed or phase or purpose or attempt):
                await self.detail_sink({**step.detail, "stage": "result"})
            await self.sink(
                {
                    **data,
                    **step.result,
                    "status": step.status,
                    "elapsed_ms": min(86400000, int((time.monotonic() - started) * 1000)),
                }
            )


class ProcessStep:
    def __init__(self, reporter, data):
        self.reporter, self.data, self.result = reporter, data, {}
        self.status = "completed"
        self.detail = {}
        self.detail_changed = False

    def fail(self, code):
        self.status = "failed"
        self.result = {"outcome": code}

    def finish(
        self,
        *,
        summary="",
        count=0,
        sources=(),
        outcome="",
        missing=None,
        questions=None,
        source_details=None,
        purpose=None,
    ):
        self.result = dict(summary=summary, count=count, sources=list(sources), outcome=outcome)
        for key, value in (
            ("missing", missing),
            ("questions", questions),
            ("sources", source_details),
            ("purpose", purpose),
        ):
            if value is not None:
                self.detail[key] = value if key == "purpose" else list(value)
                self.detail_changed = True


@asynccontextmanager
async def report_step(reporter, kind, key, *, query="", phase="", purpose="", attempt=0):
    """评测/离线宿主可显式不订阅；聊天宿主注入原Journal的报告器。"""
    if reporter is None:
        yield None
    else:
        async with reporter.step(
            kind, key, query=query, phase=phase, purpose=purpose, attempt=attempt
        ) as step:
            yield step
