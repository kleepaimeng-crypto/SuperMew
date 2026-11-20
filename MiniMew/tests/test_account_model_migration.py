import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from historical_runs import event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.account_models.schemas import ModelWrite
from minimew.account_models.service import write_model
from minimew.database import create_engine
from minimew.model_control import set_current_model
from minimew.models import AccountModelPreference, Thread, User
from minimew.settings import Settings
from minimew.skills.catalog import load_skill

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_0023_preserves_existing_active_run_and_refuses_to_drop_account_policy_or_credentials(
    model_configuration,
):
    async def scenario():
        async with temporary_database(revision="0022") as database:
            settings = Settings().model_copy(update={"db_name": database})
            engine = create_engine(settings)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="before-account", password_hash="fixture")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="旧活动对话")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    snapshot = await set_current_model(
                        session, "historical-account", model_configuration
                    )
                rid, uid, aid = uuid4(), uuid4(), uuid4()
                async with sessions() as session, session.begin():
                    await session.execute(
                        text("""INSERT INTO runs(id,user_id,thread_id,idempotency_key,
                      model_snapshot_json,model_catalog_hash,skill_request,skill_snapshot)
                      VALUES(:rid,:owner,:tid,'legacy',cast(:model AS jsonb),:hash,
                        '{}'::jsonb,cast(:skill AS jsonb))"""),
                        dict(
                            rid=rid,
                            owner=user.id,
                            tid=thread.id,
                            model=snapshot.model_dump_json(),
                            hash=snapshot.catalog_hash(),
                            skill=load_skill().model_dump_json(),
                        ),
                    )
                    for identity, role, status, sequence in [
                        (uid, "user", "completed", 1),
                        (aid, "assistant", "pending", 2),
                    ]:
                        await session.execute(
                            text("""INSERT INTO messages
                          (id,run_id,thread_id,sequence,role,status,content)
                          VALUES(:id,:rid,:tid,:seq,:role,:status,:content)"""),
                            dict(
                                id=identity,
                                rid=rid,
                                tid=thread.id,
                                seq=sequence,
                                role=role,
                                status=status,
                                content="old question" if role == "user" else "",
                            ),
                        )
                    await session.execute(
                        text("UPDATE threads SET last_sequence=2 WHERE id=:tid"),
                        dict(tid=thread.id),
                    )
                    await event(
                        session,
                        rid,
                        thread.id,
                        "run.created",
                        dict(status="pending", assistant_message_id=str(aid)),
                        1,
                    )
                migrate(database, "upgrade", "0023")
                async with sessions() as session:
                    row = (
                        await session.execute(
                            text(
                                "SELECT status,model_snapshot_json,credential_id,knowledge_enabled "
                                "FROM runs WHERE id=:id"
                            ),
                            dict(id=rid),
                        )
                    ).one()
                    assert (
                        row.status == "pending"
                        and row.credential_id is None
                        and row.knowledge_enabled
                    )
                    assert row.model_snapshot_json == json.loads(snapshot.model_dump_json())
                    assert (
                        await session.scalar(
                            text("SELECT count(*) FROM run_events WHERE run_id=:id"), dict(id=rid)
                        )
                        == 1
                    )
                    assert await session.scalar(text("SELECT count(*) FROM event_outbox")) == 1
                async with sessions() as session, session.begin():
                    session.add(
                        AccountModelPreference(
                            user_id=user.id, model_id=None, knowledge_enabled=False
                        )
                    )
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0022")
                async with sessions() as session, session.begin():
                    pref = await session.get(AccountModelPreference, user.id)
                    pref.knowledge_enabled = True
                migrate(database, "downgrade", "0022")
                migrate(database, "upgrade", "0023")
                async with sessions() as session:
                    await write_model(
                        session,
                        user.id,
                        ModelWrite(
                            name="new",
                            adapter="deepseek",
                            model_name="deepseek-flash",
                            api_key="unit-test-key",
                        ),
                        settings,
                    )
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0022")
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_new_environment_snapshot_alone_prevents_incompatible_downgrade():
    from minimew.conversation_schemas import CreateRunRequest
    from minimew.conversations import reserve_run

    async def scenario():
        async with temporary_database() as database:
            settings = Settings().model_copy(update={"db_name": database})
            engine = create_engine(settings)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="environment-only", password_hash="fixture")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="新协议")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    run, _ = await reserve_run(
                        session,
                        user.id,
                        thread.id,
                        CreateRunRequest(content="问题", idempotency_key="environment"),
                        settings=settings,
                    )
                async with sessions() as session:
                    assert await session.scalar(text("SELECT count(*) FROM model_credentials")) == 0
                    assert await session.scalar(
                        text("SELECT credential_id IS NULL FROM runs WHERE id=:id"), dict(id=run.id)
                    )
                    assert await session.scalar(
                        text(
                            "SELECT model_snapshot_json->'answer'->'configuration' ? 'adapter' "
                            "FROM runs WHERE id=:id"
                        ),
                        dict(id=run.id),
                    )
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0022")
                async with sessions() as session:
                    assert (
                        await session.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0023"
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())
