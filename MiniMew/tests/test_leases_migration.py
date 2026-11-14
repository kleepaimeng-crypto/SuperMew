"""0016→0017 保留终态/暂停/接收记录，活动旧协议拒绝迁移。"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from historical_runs import event, finish_legacy_answer, reserve_legacy_run
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_evidence import evidence, mixed

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
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PG required"
)


async def historical_wait(sessions, rid, accepted):
    async with sessions() as session, session.begin():
        tid = await session.scalar(text("SELECT thread_id FROM runs WHERE id=:rid"), dict(rid=rid))
        await session.execute(
            text("""UPDATE runs SET status='running',owner_token=:token,
            started_at=clock_timestamp(),
                            lease_until=clock_timestamp()+interval '330 seconds'
            WHERE id=:rid"""),
            dict(rid=rid, token=uuid4()),
        )
        await event(session, rid, tid, "run.started", dict(status="running"), 2)
        cid = uuid4()
        expires = await session.scalar(text("SELECT clock_timestamp()+interval '15 minutes'"))
        # 迁移数据保真测试，不把此最小 JSON 当作可执行图/模型验收。
        document = {
            "version": 1,
            "state": {"@": "dict", "v": [["remaining", 12.0]]},
            "fixture": "history",
        }
        await session.execute(
            text("""INSERT INTO run_checkpoints(id,run_id,document,prompt,expires_at)
            VALUES(:cid,:rid,cast(:doc AS jsonb),'国内还是海外？',:expires)"""),
            dict(cid=cid, rid=rid, doc=json.dumps(document), expires=expires),
        )
        await session.execute(
            text(
                "UPDATE messages SET status='waiting_input' WHERE run_id=:rid AND role='assistant'"
            ),
            dict(rid=rid),
        )
        await event(
            session,
            rid,
            tid,
            "hitl.required",
            dict(checkpoint_id=str(cid), prompt="国内还是海外？", expires_at=expires.isoformat()),
            3,
        )
        await session.execute(
            text(
                """UPDATE runs SET status='waiting_input',owner_token=NULL,lease_until=NULL
                WHERE id=:rid"""
            ),
            dict(rid=rid),
        )
        if accepted:
            await session.execute(
                text("""UPDATE run_checkpoints SET accepted_at=clock_timestamp(),
                resume_key='old-resume',input_hash=:digest,answer='国内' WHERE run_id=:rid"""),
                dict(rid=rid, digest=hashlib.sha256(b"fixture").hexdigest()),
            )
            await session.execute(
                text("UPDATE messages SET status='pending' WHERE run_id=:rid AND role='assistant'"),
                dict(rid=rid),
            )
            await event(session, rid, tid, "hitl.accepted", dict(checkpoint_id=str(cid)), 4)
            await session.execute(
                text("UPDATE runs SET status='resume_pending' WHERE id=:rid"), dict(rid=rid)
            )


@pytest.mark.parametrize("active", [False, True])
def test_0017_preserves_history_and_refuses_active_old_protocol(model_configuration, active):
    async def scenario():
        async with temporary_database(revision="0016") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="lease-history", password_hash="fixture-only")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="history")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    await set_current_model(session, "history-model", model_configuration)
                ids = []
                for i in range(4):
                    async with sessions() as session:
                        created, _ = await reserve_legacy_run(
                            session,
                            user.id,
                            thread.id,
                            CreateRunRequest(idempotency_key=str(i), content="历史问题"),
                        )
                        ids.append(created.id)
                await finish_legacy_answer(sessions, ids[0], parse_answer(mixed(), (evidence(),)))
                await historical_wait(sessions, ids[1], False)
                await historical_wait(sessions, ids[2], True)
                if active:
                    async with sessions() as session, session.begin():
                        await session.execute(
                            text("""UPDATE runs SET status='running',owner_token=:token,
                            started_at=clock_timestamp(),
                            lease_until=clock_timestamp()+interval '330 seconds'
                            WHERE id=:rid"""),
                            dict(rid=ids[3], token=uuid4()),
                        )
                        await event(
                            session, ids[3], thread.id, "run.started", dict(status="running"), 2
                        )

                async def snapshot():
                    async with engine.connect() as connection:
                        return {
                            table: (
                                await connection.execute(
                                    text(
                                        "SELECT to_jsonb(t) - 'execution_deadline_at' "
                                        f"FROM {table} t "
                                        "ORDER BY to_jsonb(t)::text"
                                    )
                                )
                            )
                            .scalars()
                            .all()
                            for table in (
                                "runs",
                                "messages",
                                "run_events",
                                "event_outbox",
                                "run_checkpoints",
                                "answer_materials",
                            )
                        }

                before = await snapshot()
                if active:
                    with pytest.raises(subprocess.CalledProcessError):
                        migrate(database, "upgrade", "head")
                    assert await snapshot() == before
                    async with engine.connect() as connection:
                        assert (
                            await connection.scalar(text("SELECT version_num FROM alembic_version"))
                            == "0016"
                        )
                        assert (
                            await connection.scalar(
                                text(
                                    """SELECT count(*) FROM information_schema.columns
                                    WHERE table_name='runs'
                                      AND column_name='execution_deadline_at'"""
                                )
                            )
                            == 0
                        )
                else:
                    migrate(database, "upgrade", "head")
                    migrate(database, "check")
                    assert await snapshot() == before
                    async with engine.connect() as connection:
                        assert (
                            await connection.scalar(
                                text(
                                    "SELECT count(*) FROM runs "
                                    "WHERE execution_deadline_at IS NOT NULL"
                                )
                            )
                            == 0
                        )
            finally:
                await engine.dispose()

    asyncio.run(scenario())
