"""0021活动协议明确拒绝升级；旧终态无Skill，新Run必须冻结且历史拒降级。"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from historical_runs import event, reserve_legacy_run
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.conversation_schemas import CreateRunRequest
from minimew.conversations import get_run, reserve_run
from minimew.database import create_engine
from minimew.model_control import set_current_model
from minimew.models import Thread, User
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_migration_rejects_active_run_preserves_old_terminal_and_blocks_frozen_history_downgrade(
    model_configuration,
):
    async def scenario():
        async with temporary_database(revision="0021") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="before_skills", password_hash="fixture")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="历史")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    await set_current_model(session, "before-skills", model_configuration)
                data = CreateRunRequest(idempotency_key="before", content="旧请求")
                async with sessions() as session:
                    run, _ = await reserve_legacy_run(session, user.id, thread.id, data)
                rid = UUID(str(run.id))
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "upgrade", "head")
                async with sessions() as session, session.begin():
                    assert (
                        await session.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0021"
                    )
                    aid = await session.scalar(
                        text("SELECT id FROM messages WHERE run_id=:rid AND role='assistant'"),
                        dict(rid=rid),
                    )
                    cancelled_at = await session.scalar(select(func.clock_timestamp()))
                    await event(
                        session,
                        rid,
                        thread.id,
                        "run.cancel_requested",
                        dict(cancel_requested_at=cancelled_at.isoformat()),
                        2,
                    )
                    await session.execute(
                        text("UPDATE messages SET status='cancelled' WHERE id=:aid"), dict(aid=aid)
                    )
                    await event(
                        session,
                        rid,
                        thread.id,
                        "message.completed",
                        dict(message_id=str(aid), status="cancelled", content=""),
                        3,
                    )
                    await event(
                        session, rid, thread.id, "run.cancelled", dict(status="cancelled"), 4
                    )
                    await session.execute(
                        text(
                            "UPDATE runs SET status='cancelled',"
                            "cancel_requested_at=:cancelled_at,"
                            "finished_at=clock_timestamp() WHERE id=:rid"
                        ),
                        dict(rid=rid, cancelled_at=cancelled_at),
                    )

                async def facts():
                    async with engine.connect() as connection:
                        return {
                            table: (
                                await connection.execute(
                                    text(
                                        "SELECT to_jsonb(t) - 'skill_snapshot' - 'skill_request' "
                                        f"FROM {table} t"
                                    )
                                )
                            )
                            .scalars()
                            .all()
                            for table in ("runs", "messages", "run_events", "event_outbox")
                        }

                before = await facts()
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                assert await facts() == before
                async with sessions() as session:
                    assert (await get_run(session, user.id, thread.id, rid)).skill is None
                async with sessions() as session:
                    replay, created = await reserve_run(session, user.id, thread.id, data)
                    assert not created and replay.id == rid and replay.skill is None
                migrate(database, "downgrade", "0021")
                assert await facts() == before
                migrate(database, "upgrade", "head")
                async with sessions() as session:
                    fresh, created = await reserve_run(
                        session,
                        user.id,
                        thread.id,
                        CreateRunRequest(idempotency_key="fresh", content="新请求"),
                    )
                    assert created and fresh.skill.id == "default"
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0021")
            finally:
                await engine.dispose()

    asyncio.run(scenario())
