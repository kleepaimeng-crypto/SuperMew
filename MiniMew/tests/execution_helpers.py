"""只供隔离验收注入的 Provider；没有网络、默认应用配置或全局请求状态。"""

from contextlib import aclosing, asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import ExecutionRepository
from minimew.models import Run
from minimew.settings import Settings


async def activate_claim(repo, claim):
    """直接驱动存储的测试也遵守正式的 claim → activate 协议。"""
    request = await repo.request(claim)
    await repo.activate(claim, request.snapshot.answer.configuration.timeout_seconds)


async def lease_remaining(repo, claim):
    """资格过期测试等 PG 租约；remaining() 现在专指活动预算。"""
    async with repo.sessions() as session:
        duration = await session.scalar(
            select(Run.lease_until - func.clock_timestamp()).where(
                Run.id == claim.run_id, Run.owner_token == claim.owner_token
            )
        )
        return duration.total_seconds() if duration else 0


class FakeProvider:
    def __init__(self, handler=None):
        self.handler = handler
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        if self.handler is not None:
            async with aclosing(self.handler(request)) as stream:
                async for event in stream:
                    yield event
        else:
            yield ProviderCompleted(ModelTurn("text", "answer:" + request.messages[-1].content))


@asynccontextmanager
async def repository(**kwargs):
    engine = create_engine(Settings())
    try:
        yield ExecutionRepository(async_sessionmaker(engine, expire_on_commit=False), **kwargs)
    finally:
        await engine.dispose()
