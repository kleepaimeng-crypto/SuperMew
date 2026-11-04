"""与 API 共用配置，但迁移由独立命令显式执行。"""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from minimew.database import database_url
from minimew.models import Base
from minimew.settings import Settings

target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_server_default=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(database_url(Settings()), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql", target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
