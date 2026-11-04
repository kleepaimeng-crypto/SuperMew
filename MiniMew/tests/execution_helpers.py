"""只供隔离验收注入的 Provider；没有网络、默认应用配置或全局请求状态。"""

from contextlib import aclosing, asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import ExecutionRepository
from minimew.settings import Settings


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
            yield ProviderCompleted("answer:" + request.messages[-1].content)


@asynccontextmanager
async def repository(**kwargs):
    engine = create_engine(Settings())
    try:
        yield ExecutionRepository(async_sessionmaker(engine, expire_on_commit=False), **kwargs)
    finally:
        await engine.dispose()
