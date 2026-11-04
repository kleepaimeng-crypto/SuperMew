"""应用共享 Engine；请求独立 Session；事务由调用方明确控制。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from minimew.settings import Settings


def database_url(settings: Settings) -> URL:
    # URL.create 正确处理密码中的 @、% 等字符，不手工拼接 DSN。
    return URL.create(
        "postgresql+asyncpg",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )


def create_engine(settings: Settings):
    return create_async_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_timeout=settings.db_timeout_seconds,
        connect_args={
            "timeout": settings.db_timeout_seconds,
            "command_timeout": settings.db_timeout_seconds,
        },
        hide_parameters=True,
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        # close 会释放连接并回滚未提交事务，不在依赖中自动 commit。
        yield session


SessionDependency = Annotated[AsyncSession, Depends(get_session)]
