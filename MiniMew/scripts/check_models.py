"""显式 PostgreSQL 集成检查，在随机临时数据库内运行并清理，不清空开发库。"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.model_control import ModelSnapshotUnavailable, snapshot_for_run
from minimew.models import Run, RunEventRecord, Thread, User
from minimew.security import hash_password
from minimew.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def migrate(database: str, *args):
    env = dict(os.environ, MINIMEW_DB_NAME=database)
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=30,
    )


async def check_models(settings: Settings):
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0021"
            )
            indexes = await connection.run_sync(lambda c: inspect(c).get_indexes("threads"))
            assert any(i["column_names"] == ["user_id"] for i in indexes)
        async with factory() as session:
            async with session.begin():
                owner = User(
                    username="model_owner", password_hash=hash_password("test-password-1234")
                )
                session.add(owner)
                await session.flush()
                thread = Thread(user_id=owner.id, title="数据库验收")
                session.add(thread)
                await session.flush()
                assert isinstance(owner.id, UUID) and isinstance(thread.id, UUID)
                assert thread.created_at.tzinfo is not None
                assert owner.created_at.tzinfo is not None
                owner_id, thread_id = owner.id, thread.id
            async with session.begin():
                assert (await session.get(Thread, thread_id)).user_id == owner_id

            # 验证数据库约束，而不是依赖 Python 输入模型。
            for invalid_owner, expected_state in [(uuid4(), "23503"), (None, "23502")]:
                try:
                    async with session.begin():
                        session.add(Thread(user_id=invalid_owner, title="invalid"))
                        await session.flush()
                except IntegrityError as error:
                    assert error.orig.sqlstate == expected_state
                else:
                    raise AssertionError("Invalid owner was accepted")

            try:
                async with session.begin():
                    await session.execute(delete(User).where(User.id == owner_id))
            except IntegrityError as error:
                assert error.orig.sqlstate in {"23001", "23503"}
            else:
                raise AssertionError("Owner with threads was deleted")

            async with session.begin():
                assert (
                    await session.scalars(select(Thread).where(Thread.id == thread_id))
                ).all() == [thread]
                assert await session.get(User, owner_id) is not None
        print("PASS: ORM round trip, UUID/timestamps, owner FK/NOT NULL, delete protection, index")
    finally:
        await engine.dispose()


async def check_legacy_block(settings: Settings, database: str):
    migrate(database, "upgrade", "0001")
    migrate(database, "upgrade", "0002")
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            legacy_id = await connection.scalar(
                text("INSERT INTO users DEFAULT VALUES RETURNING id")
            )
            await connection.execute(
                text("INSERT INTO threads(user_id, title) VALUES (:id, 'legacy')"),
                {"id": legacy_id},
            )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=dict(os.environ, MINIMEW_DB_NAME=database),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "Existing users require an explicit credential migration plan" in result.stderr
        async with engine.begin() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
            )
            assert await connection.scalar(text("SELECT count(*) FROM users")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM threads")) == 1
            columns = await connection.run_sync(lambda c: inspect(c).get_columns("users"))
            assert "password_hash" not in {column["name"] for column in columns}
            # 仅清除本次创建的临时测试库记录；真实旧用户绝不自动删除。
            await connection.execute(text("DELETE FROM threads"))
            await connection.execute(text("DELETE FROM users"))
        print("PASS: legacy users block migration atomically; users/threads/version preserved")
    finally:
        await engine.dispose()


async def seed_legacy_terminal(engine, old_run, old_thread):
    # 用旧 schema 的 SQL 构造历史事实，不用新 Journal 写旧库。
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE runs SET status='running', owner_token=gen_random_uuid(),
            started_at=clock_timestamp(),
            lease_until=clock_timestamp()+interval '5 minutes',
            last_event_sequence=1 WHERE id=:run
        """),
            {"run": old_run},
        )
        await connection.execute(
            text("""
            INSERT INTO run_events(event_id,run_id,thread_id,sequence,type,timestamp,data)
            VALUES (gen_random_uuid(),:run,:thread,1,'run.started',clock_timestamp(),
                    '{"status":"running"}'::jsonb)
        """),
            {"run": old_run, "thread": old_thread},
        )
        await connection.execute(
            text("""
            UPDATE messages SET status='failed' WHERE run_id=:run AND role='assistant'
        """),
            {"run": old_run},
        )
        await connection.execute(
            text("""
            INSERT INTO run_events(event_id,run_id,thread_id,sequence,type,timestamp,data)
            SELECT gen_random_uuid(),run_id,thread_id,2,'message.completed',clock_timestamp(),
            jsonb_build_object('message_id',id::text,'status','failed','content',content)
            FROM messages WHERE run_id=:run AND role='assistant'
        """),
            {"run": old_run},
        )
        await connection.execute(
            text("""
            INSERT INTO run_events(event_id,run_id,thread_id,sequence,type,timestamp,data)
            VALUES (gen_random_uuid(),:run,:thread,3,'run.failed',clock_timestamp(),
            '{"status":"failed","error_code":"MODEL_SNAPSHOT_MISSING"}'::jsonb)
        """),
            {"run": old_run, "thread": old_thread},
        )
        await connection.execute(
            text("""
            UPDATE runs SET status='failed',finished_at=clock_timestamp(),
            error_code='MODEL_SNAPSHOT_MISSING',last_event_sequence=3 WHERE id=:run
        """),
            {"run": old_run},
        )


async def create_legacy_pending(engine, owner):
    """迁移前创建无快照记录，升级后由正式执行器处理。"""
    async with engine.begin() as connection:
        thread = await connection.scalar(
            text("""
            INSERT INTO threads(user_id,title) VALUES (:owner,'legacy pending') RETURNING id
        """),
            {"owner": owner},
        )
        run = await connection.scalar(
            text("""
            INSERT INTO runs(thread_id,user_id,idempotency_key)
            VALUES (:thread,:owner,'legacy-pending') RETURNING id
        """),
            {"thread": thread, "owner": owner},
        )
        await connection.execute(
            text("""
            INSERT INTO messages(thread_id,run_id,sequence,role,status,content)
            VALUES (:thread,:run,1,'user','completed','legacy'),
                   (:thread,:run,2,'assistant','pending','')
        """),
            {"thread": thread, "run": run},
        )
        await connection.execute(
            text("UPDATE threads SET last_sequence=2 WHERE id=:thread"), {"thread": thread}
        )

    return thread, run


async def check_missing_snapshot_execution(engine, owner, thread, run):
    """升级后仍以正式执行器证明无快照 Run 不会调用模型。"""
    from minimew.execution.repository import ExecutionRepository
    from minimew.execution.service import RunExecutor

    class MustNotCallProvider:
        called = False

        def stream(self, request):
            self.called = True
            raise AssertionError("Legacy Run must never call a Provider")

    forbidden = MustNotCallProvider()
    repository = ExecutionRepository(async_sessionmaker(engine, expire_on_commit=False))
    assert await RunExecutor(repository, forbidden).execute(owner, thread, run) == "failed"
    assert not forbidden.called
    async with engine.connect() as connection:
        assert (
            await connection.scalar(select(Run.error_code).where(Run.id == run))
            == "MODEL_SNAPSHOT_MISSING"
        )
    print("PASS: legacy Run fails explicitly without calling Provider")


async def main(test_files=None):
    settings = Settings()
    if (settings.db_host, settings.db_port, settings.db_name, settings.db_user) != (
        "127.0.0.1",
        55432,
        "minimew",
        "minimew",
    ):
        raise SystemExit("Only the default local MiniMew database server is supported")
    admin = create_engine(settings)
    try:
        for scenario in ["empty", "legacy"]:
            # 名称仅由固定前缀和 UUID hex 构成；只删除本次成功创建的临时库。
            test_database = "minimew_test_" + uuid4().hex
            created = False
            try:
                async with admin.connect() as connection:
                    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(text(f'CREATE DATABASE "{test_database}"'))
                    created = True
                test_settings = settings.model_copy(update={"db_name": test_database})
                if scenario == "legacy":
                    await check_legacy_block(test_settings, test_database)
                    migrate(test_database, "upgrade", "0003")
                    engine = create_engine(test_settings)
                    try:
                        async with engine.begin() as connection:
                            existing_user = await connection.scalar(
                                insert(User)
                                .values(
                                    username="existing_upgrade_user",
                                    password_hash=hash_password("test-existing-password-1234"),
                                )
                                .returning(User.id)
                            )
                        migrate(test_database, "upgrade", "0004")
                        async with engine.begin() as connection:
                            old_thread = await connection.scalar(
                                text(
                                    "INSERT INTO threads(user_id, title) "
                                    "VALUES (:owner, 'preserved') RETURNING id"
                                ),
                                {"owner": existing_user},
                            )
                        migrate(test_database, "upgrade", "0005")
                        # 构造 2.1 已存在的合法消息对，0006 必须保留且不补猜模型。
                        async with engine.begin() as connection:
                            old_run = await connection.scalar(
                                text(
                                    "INSERT INTO runs(thread_id,user_id,idempotency_key) "
                                    "VALUES (:thread,:owner,'legacy-run') RETURNING id"
                                ),
                                {"thread": old_thread, "owner": existing_user},
                            )
                            await connection.execute(
                                text(
                                    "INSERT INTO messages"
                                    "(thread_id,run_id,sequence,role,status,content) "
                                    "VALUES (:thread,:run,1,'user','completed',"
                                    "'preserved question'), "
                                    "(:thread,:run,2,'assistant','pending','')"
                                ),
                                {"thread": old_thread, "run": old_run},
                            )
                            await connection.execute(
                                text("UPDATE threads SET last_sequence=2 WHERE id=:thread"),
                                {"thread": old_thread},
                            )
                        legacy_pending = await create_legacy_pending(engine, existing_user)
                        migrate(test_database, "upgrade", "0007")
                        async with engine.connect() as connection:
                            assert (
                                await connection.scalar(
                                    select(User.id).where(User.username == "existing_upgrade_user")
                                )
                                == existing_user
                            )
                            old_row = (
                                await connection.execute(
                                    select(Thread.title, Thread.last_sequence).where(
                                        Thread.id == old_thread
                                    )
                                )
                            ).one()
                            assert old_row == ("preserved", 2)
                            assert (
                                await connection.scalar(text("SELECT count(*) FROM messages")) == 4
                            )
                        async with async_sessionmaker(engine)() as session:
                            # 历史 schema 不含后来新增的取消列，显式读取当时已有字段。
                            old = (
                                await session.execute(
                                    select(
                                        Run.model_snapshot_json,
                                        Run.model_catalog_hash,
                                    ).where(Run.id == old_run)
                                )
                            ).one()
                            assert (
                                old.model_snapshot_json is None and old.model_catalog_hash is None
                            )
                            try:
                                snapshot_for_run(old)
                            except ModelSnapshotUnavailable as error:
                                assert error.code == "MODEL_SNAPSHOT_MISSING"
                            else:
                                raise AssertionError(
                                    "Legacy Run must not obtain current configuration"
                                )
                        async with engine.begin() as connection:
                            try:
                                async with connection.begin_nested():
                                    await connection.execute(
                                        text(
                                            "UPDATE runs SET model_snapshot_json='{}', "
                                            "model_catalog_hash=repeat('0',64) WHERE id=:run"
                                        ),
                                        {"run": old_run},
                                    )
                            except IntegrityError as error:
                                assert error.orig.sqlstate == "23514"
                            else:
                                raise AssertionError(
                                    "Legacy snapshot must not be backfilled implicitly"
                                )
                        print(
                            "PASS: registered user, Thread and legacy Run/messages preserved; "
                            "missing snapshot rejected and cannot be backfilled"
                        )
                        await seed_legacy_terminal(engine, old_run, old_thread)
                        async with engine.connect() as connection:
                            before_events = (
                                await connection.execute(
                                    select(RunEventRecord)
                                    .where(RunEventRecord.run_id == old_run)
                                    .order_by(RunEventRecord.sequence)
                                )
                            ).all()
                        migrate(test_database, "upgrade", "head")
                        async with engine.connect() as connection:
                            after_events = (
                                await connection.execute(
                                    select(RunEventRecord)
                                    .where(RunEventRecord.run_id == old_run)
                                    .order_by(RunEventRecord.sequence)
                                )
                            ).all()
                            assert before_events == after_events and len(after_events) == 3
                            assert (
                                await connection.scalar(text("SELECT count(*) FROM event_outbox"))
                                == 0
                            )
                        print(
                            "PASS: 0007 terminal events preserved through 0010; "
                            "no historical outbox backfill"
                        )
                        await check_missing_snapshot_execution(
                            engine, existing_user, *legacy_pending
                        )
                    finally:
                        await engine.dispose()
                migrate(test_database, "upgrade", "head")
                migrate(test_database, "upgrade", "head")
                migrate(test_database, "check")
                await check_models(test_settings)
                if scenario == "empty" and test_files != []:
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            "--tb=short",
                            *(
                                test_files
                                if test_files is not None
                                else [
                                    "tests/test_auth_pg.py",
                                    "tests/test_refresh_pg.py",
                                    "tests/test_threads_pg.py",
                                    "tests/test_runs_pg.py",
                                    "tests/test_execution_pg.py",
                                    "tests/test_events_pg.py",
                                    "tests/test_sse_pg.py",
                                    "tests/test_stream_reads_pg.py",
                                    "tests/test_outbox_pg.py",
                                    "tests/test_cancellation_pg.py",
                                ]
                            ),
                        ],
                        cwd=ROOT,
                        env=dict(os.environ, MINIMEW_INTEGRATION_DB=test_database),
                        check=True,
                        timeout=120,
                    )
                print(f"PASS: {scenario} migration path and metadata consistency")
            finally:
                if created:
                    async with admin.connect() as connection:
                        connection = await connection.execution_options(
                            isolation_level="AUTOCOMMIT"
                        )
                        await connection.execute(text(f'DROP DATABASE "{test_database}"'))
                    print("Cleaned up owned temporary database")
    finally:
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
