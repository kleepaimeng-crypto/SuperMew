"""在真实 0010 中建立旧对话，再升级当前 head 并验证原样保留、ORM 无漂移。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.conversation_schemas import CreateRunRequest
from minimew.conversations import reserve_run
from minimew.database import create_engine
from minimew.model_control import set_current_model
from minimew.models import Thread, User
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit owned temporary PostgreSQL required",
)


def test_0010_upgrade_preserves_conversation_and_has_no_schema_drift(model_configuration):
    async def scenario():
        async with temporary_database(revision="0010") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            tables = [
                "users",
                "threads",
                "runs",
                "messages",
                "run_events",
                "event_outbox",
                "model_profiles",
                "model_assignments",
                "refresh_tokens",
            ]
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    async with session.begin():
                        user = User(username="legacy_document_owner", password_hash="fixture-only")
                        session.add(user)
                        await session.flush()
                        thread = Thread(user_id=user.id, title="升级前对话")
                        session.add(thread)
                        await session.flush()
                    await set_current_model(session, "fixture", model_configuration)
                    await reserve_run(
                        session,
                        user.id,
                        thread.id,
                        CreateRunRequest(content="保留原问题", idempotency_key="before-upgrade"),
                    )
                async with engine.connect() as connection:
                    before = {
                        table: (
                            await connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t"))
                        )
                        .scalars()
                        .all()
                        for table in tables
                    }
                migrate(database, "upgrade", "head")
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                async with engine.connect() as connection:
                    for table in tables:
                        after = (
                            (await connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t")))
                            .scalars()
                            .all()
                        )
                        assert after == before[table], table
                    assert (
                        await connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0014"
                    )
                    actual = await connection.run_sync(lambda c: inspect(c).get_table_names())
                    assert {
                        "knowledge_bases",
                        "documents",
                        "document_versions",
                        "index_jobs",
                        "document_submissions",
                    }.issubset(actual)
                    for table in [
                        "knowledge_bases",
                        "documents",
                        "document_versions",
                        "index_jobs",
                    ]:
                        assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
            finally:
                await engine.dispose()

    asyncio.run(scenario())
