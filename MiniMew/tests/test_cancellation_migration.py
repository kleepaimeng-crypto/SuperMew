"""先在真实 0009 schema 构造旧事实，再验证 0010 原样保留；不依赖新 ORM 建旧记录。"""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from minimew.database import create_engine
from minimew.model_control import ModelSnapshot, ProfileSnapshot
from minimew.security import hash_password
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit local migration test required",
)


async def append(connection, run, thread, sequence, kind, data):
    event = uuid4()
    await connection.execute(
        text("UPDATE runs SET last_event_sequence=:seq WHERE id=:run"),
        {"seq": sequence, "run": run},
    )
    await connection.execute(
        text("""
        INSERT INTO run_events(event_id,run_id,thread_id,sequence,type,timestamp,data)
        VALUES (:id,:run,:thread,:seq,:kind,clock_timestamp(),CAST(:data AS jsonb))
    """),
        {
            "id": event,
            "run": run,
            "thread": thread,
            "seq": sequence,
            "kind": kind,
            "data": json.dumps(data),
        },
    )
    await connection.execute(text("INSERT INTO event_outbox(event_id) VALUES (:id)"), {"id": event})


async def seed_0009(connection, configuration):
    snapshot = ModelSnapshot(
        answer=ProfileSnapshot(
            profile_id=uuid4(),
            profile_version=1,
            configuration=configuration,
        )
    )
    user = await connection.scalar(
        text("""
        INSERT INTO users(username,password_hash) VALUES ('upgrade_owner',:hash) RETURNING id
    """),
        {"hash": hash_password("migration-test-password")},
    )
    for state in ("pending", "running", "succeeded", "failed"):
        thread = await connection.scalar(
            text("""
            INSERT INTO threads(user_id,title,last_sequence)
            VALUES (:user,'preserved',2) RETURNING id
        """),
            {"user": user},
        )
        run = await connection.scalar(
            text("""
            INSERT INTO runs(
                thread_id,user_id,idempotency_key,model_snapshot_json,model_catalog_hash)
            VALUES (:thread,:user,:key,CAST(:snapshot AS jsonb),:hash) RETURNING id
        """),
            {
                "thread": thread,
                "user": user,
                "key": state,
                "snapshot": snapshot.model_dump_json(),
                "hash": snapshot.catalog_hash(),
            },
        )
        await connection.execute(
            text("""
            INSERT INTO messages(thread_id,run_id,sequence,role,status,content)
            VALUES (:thread,:run,1,'user','completed','question'),
                   (:thread,:run,2,'assistant','pending','')
        """),
            {"thread": thread, "run": run},
        )
        answer = await connection.scalar(
            text("""
            SELECT id FROM messages WHERE run_id=:run AND role='assistant'
        """),
            {"run": run},
        )
        await append(
            connection,
            run,
            thread,
            1,
            "run.created",
            {"status": "pending", "assistant_message_id": str(answer)},
        )
        if state == "pending":
            continue
        await connection.execute(
            text("""
            UPDATE runs SET status='running',owner_token=gen_random_uuid(),
                started_at=clock_timestamp(),lease_until=clock_timestamp()+interval '10 minutes'
            WHERE id=:run
        """),
            {"run": run},
        )
        await append(connection, run, thread, 2, "run.started", {"status": "running"})
        await append(
            connection,
            run,
            thread,
            3,
            "message.delta",
            {"message_id": str(answer), "content": "旧正文"},
        )
        if state == "running":
            continue
        status = "completed" if state == "succeeded" else "failed"
        await connection.execute(
            text("""
            UPDATE messages SET content='旧正文',status=:status WHERE id=:answer
        """),
            {"status": status, "answer": answer},
        )
        await append(
            connection,
            run,
            thread,
            4,
            "message.completed",
            {"message_id": str(answer), "status": status, "content": "旧正文"},
        )
        data = {"status": state}
        if state == "failed":
            data["error_code"] = "PROVIDER_ERROR"
        await append(
            connection,
            run,
            thread,
            5,
            "run.completed" if state == "succeeded" else "run.failed",
            data,
        )
        await connection.execute(
            text("""
            UPDATE runs SET status=:status,error_code=:error,finished_at=clock_timestamp()
            WHERE id=:run
        """),
            {"status": state, "run": run, "error": data.get("error_code")},
        )


def test_0009_upgrade_preserves_all_states_snapshots_prefix_and_outbox(model_configuration):
    async def scenario():
        async with temporary_database(revision="0009") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            tables = ("users", "threads", "runs", "messages", "run_events", "event_outbox")
            try:
                async with engine.begin() as connection:
                    await seed_0009(connection, model_configuration)
                async with engine.connect() as connection:
                    columns = await connection.run_sync(
                        lambda c: {
                            table: [x["name"] for x in inspect(c).get_columns(table)]
                            for table in tables
                        }
                    )
                    queries = {
                        table: text(
                            "SELECT "
                            + ",".join(columns[table])
                            + " FROM "
                            + table
                            + " ORDER BY "
                            + columns[table][0]
                        )
                        for table in tables
                    }
                    before = {
                        table: (await connection.execute(query)).all()
                        for table, query in queries.items()
                    }
                migrate(database, "upgrade", "head")
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                async with engine.connect() as connection:
                    for table, query in queries.items():
                        assert (await connection.execute(query)).all() == before[table]
                    assert (
                        await connection.scalar(
                            text("SELECT count(*) FROM runs WHERE cancel_requested_at IS NOT NULL")
                        )
                        == 0
                    )
                    assert (
                        await connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0016"
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())
