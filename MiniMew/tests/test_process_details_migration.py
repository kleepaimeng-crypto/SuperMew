"""0020旧过程不改写；0021元信息沿用Journal且有历史时拒绝降级。"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from historical_runs import event, reserve_legacy_run
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.conversation_schemas import CreateRunRequest
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


def test_old_process_journal_survives_upgrade_and_detail_history_blocks_downgrade(
    model_configuration,
):
    async def scenario():
        async with temporary_database(revision="0020") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="before_process_details", password_hash="fixture")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="旧过程")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    await set_current_model(session, "before-details", model_configuration)
                async with sessions() as session:
                    run, _ = await reserve_legacy_run(
                        session,
                        user.id,
                        thread.id,
                        CreateRunRequest(idempotency_key="old-process", content="准备工作"),
                    )
                # 历史协议仅用SQL夹具；最新Run ORM已有0022字段，不能用于0020。
                rid = UUID(str(run.id))
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE runs SET status='running',owner_token=:token,"
                            "started_at=clock_timestamp(),"
                            "lease_until=clock_timestamp()+interval '20 seconds' "
                            "WHERE id=:rid"
                        ),
                        dict(token=uuid4(), rid=rid),
                    )
                    await event(session, rid, thread.id, "run.started", {"status": "running"}, 2)
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE runs SET execution_deadline_at=clock_timestamp()+ "
                            "((model_snapshot_json#>>"
                            "'{answer,configuration,timeout_seconds}')::float "
                            "- 1) "
                            "* interval '1 second' "
                            "WHERE id=:rid"
                        ),
                        dict(rid=rid),
                    )
                    step = dict(
                        step_id="a" * 32,
                        parent_step_id="",
                        tool_call_id="",
                        kind="model",
                        status="running",
                        query="",
                        summary="",
                        count=0,
                        sources=[],
                        outcome="",
                        elapsed_ms=0,
                    )
                    await event(session, rid, thread.id, "process.step", step, 3)
                    await event(
                        session, rid, thread.id, "process.step", {**step, "status": "completed"}, 4
                    )

                async def snapshot():
                    async with engine.connect() as connection:
                        return (
                            (
                                await connection.execute(
                                    text("SELECT to_jsonb(e) FROM run_events e ORDER BY sequence")
                                )
                            )
                            .scalars()
                            .all()
                        )

                before = await snapshot()
                migrate(database, "upgrade", "0021")
                assert await snapshot() == before
                migrate(database, "downgrade", "0020")
                assert await snapshot() == before
                migrate(database, "upgrade", "0021")

                async with sessions() as session, session.begin():
                    child = {**step, "step_id": "b" * 32, "kind": "assessment"}
                    await event(session, rid, thread.id, "process.step", child, 5)
                    detail = dict(
                        step_id=child["step_id"],
                        stage="result",
                        phase="initial",
                        purpose="",
                        attempt=0,
                        missing=["缺少备份路径"],
                        questions=[],
                        sources=[],
                    )
                    await event(session, rid, thread.id, "process.detail", detail, 6)
                    await event(
                        session, rid, thread.id, "process.step", {**child, "status": "completed"}, 7
                    )
                    aid = await session.scalar(
                        text("SELECT id FROM messages WHERE run_id=:rid AND role='assistant'"),
                        dict(rid=rid),
                    )
                    await session.execute(
                        text("UPDATE messages SET status='completed',content='done' WHERE id=:aid"),
                        dict(aid=aid),
                    )
                    await event(
                        session,
                        rid,
                        thread.id,
                        "message.completed",
                        dict(message_id=str(aid), content="done", status="completed"),
                        8,
                    )
                    await event(
                        session, rid, thread.id, "run.completed", dict(status="succeeded"), 9
                    )
                    await session.execute(
                        text(
                            "UPDATE runs SET status='succeeded',"
                            "finished_at=clock_timestamp() WHERE id=:rid"
                        ),
                        dict(rid=rid),
                    )
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0020")
                assert (await snapshot())[: len(before)] == before
            finally:
                await engine.dispose()

    asyncio.run(scenario())
