"""0014 旧 Run、快照、正文、引用和 Journal 升级 0015 原样保留。"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest
from historical_runs import finish_legacy_answer, reserve_legacy_run
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_evidence import evidence, mixed

from minimew.answers import read_answer
from minimew.conversation_schemas import CreateRunRequest
from minimew.database import create_engine
from minimew.evidence import parse_answer
from minimew.model_control import set_current_model
from minimew.models import Thread, User
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PostgreSQL required"
)


def test_0014_answer_materials_and_journal_survive_0015(model_configuration):
    async def scenario():
        async with temporary_database(revision="0014") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="before_tool_events", password_hash="fixture-no-login")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="旧答案")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    await set_current_model(session, "before-tool-model", model_configuration)
                async with sessions() as session:
                    created, _ = await reserve_legacy_run(
                        session,
                        user.id,
                        thread.id,
                        CreateRunRequest(idempotency_key="old", content="旧问题"),
                        credentials_available=True,
                    )
                rid = UUID(str(created.id))
                answer = parse_answer(mixed(), (evidence(),))
                await finish_legacy_answer(sessions, rid, answer)

                async def snapshot():
                    async with engine.connect() as conn:
                        return {
                            table: (
                                await conn.execute(
                                    text(
                                        f"SELECT (to_jsonb(t) - 'execution_deadline_at') "
                                        f"FROM {table} t"
                                    )
                                )
                            )
                            .scalars()
                            .all()
                            for table in (
                                "runs",
                                "messages",
                                "answer_materials",
                                "run_events",
                                "event_outbox",
                            )
                        }

                before = await snapshot()
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                assert await snapshot() == before
                async with sessions() as session:
                    read = await read_answer(session, user.id, thread.id, rid)
                    assert read.materials == answer.materials
            finally:
                await engine.dispose()

    asyncio.run(scenario())
