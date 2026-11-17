"""仅验收使用：在已提交暂停/接收边界退出，下一进程必须读取PG续接。"""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.evaluation.store import EvaluationStore
from minimew.evaluation.worker import EvaluationWorker
from minimew.settings import Settings


class PhaseSaved(BaseException):
    pass


class BoundaryStore(EvaluationStore):
    def __init__(self, sessions, phase):
        super().__init__(sessions)
        self.phase = phase

    async def write_case(self, *args, **kwargs):
        await super().write_case(*args, **kwargs)
        if self.phase == "waiting" and args[3] == "waiting":
            raise PhaseSaved

    async def accept(self, *args, **kwargs):
        accepted = await super().accept(*args, **kwargs)
        if accepted and self.phase == "accepted":
            raise PhaseSaved
        return accepted


async def main(phase):
    engine = create_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    worker = EvaluationWorker(sessions)
    worker.store = BoundaryStore(sessions, phase)
    try:
        try:
            assert await worker.once() or phase == "cancelled", "No owned Job claimed"
        except PhaseSaved:
            assert phase != "completed"
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("waiting", "accepted", "completed", "cancelled"))
    asyncio.run(main(parser.parse_args().phase))
