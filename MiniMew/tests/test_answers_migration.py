"""0012真实旧消息升级0013，不补造Evidence或改写Journal。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
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
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def test_0012_history_upgrades_unchanged_without_fabricated_materials(model_configuration):
    async def scenario():
        async with temporary_database(revision="0012") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    owner = User(username="before_answers", password_hash="fixture-no-login")
                    session.add(owner)
                    await session.flush()
                    thread = Thread(user_id=owner.id, title="旧对话")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    await set_current_model(session, "before-answer-model", model_configuration)
                async with sessions() as session:
                    await reserve_run(
                        session,
                        owner.id,
                        thread.id,
                        CreateRunRequest(idempotency_key="old", content="旧问题"),
                        credentials_available=True,
                    )
                tables = ("users", "threads", "runs", "messages", "run_events", "event_outbox")

                async def snapshot():
                    async with engine.connect() as conn:
                        return {
                            table: (await conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")))
                            .scalars()
                            .all()
                            for table in tables
                        }

                before = await snapshot()
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                assert await snapshot() == before
                async with engine.connect() as conn:
                    assert (
                        await conn.scalar(text("SELECT version_num FROM alembic_version")) == "0016"
                    )
                    assert await conn.scalar(text("SELECT count(*) FROM answer_materials")) == 0
            finally:
                await engine.dispose()

    asyncio.run(scenario())
