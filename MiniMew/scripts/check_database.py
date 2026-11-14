"""显式集成检查：仅针对 MiniMew 本地库；可选短暂停库后自动恢复。"""

import argparse
import asyncio
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from minimew.app import create_app
from minimew.database import create_engine
from minimew.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


async def check_transactions():
    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "0017", revision
            tables = (
                (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                )
                .scalars()
                .all()
            )
            assert set(tables) == {
                "alembic_version",
                "users",
                "threads",
                "refresh_tokens",
                "runs",
                "run_checkpoints",
                "messages",
                "answer_materials",
                "model_profiles",
                "model_assignments",
                "run_events",
                "event_outbox",
                "knowledge_bases",
                "documents",
                "document_versions",
                "index_jobs",
                "document_submissions",
                "index_artifacts",
                "index_cleanup_jobs",
                "index_worker_heartbeats",
            }, tables
            # 临时表只存在于本连接，用于验证事务，不增加业务 schema。
            await connection.execute(text("CREATE TEMP TABLE transaction_probe (value integer)"))
            await connection.commit()
            async with AsyncSession(bind=connection) as session:
                async with session.begin():
                    await session.execute(text("INSERT INTO transaction_probe VALUES (1)"))
                try:
                    async with session.begin():
                        await session.execute(text("INSERT INTO transaction_probe VALUES (2)"))
                        raise ValueError("simulate business failure")
                except ValueError:
                    pass
                assert (
                    await session.execute(
                        text("SELECT value FROM transaction_probe ORDER BY value")
                    )
                ).scalars().all() == [1]
            async with AsyncSession(bind=connection) as session:
                await session.execute(text("INSERT INTO transaction_probe VALUES (3)"))
                # 未提交关闭 Session，也必须回滚。
            assert await connection.scalar(text("SELECT count(*) FROM transaction_probe")) == 1
        print("PASS: revision 0017; expected tables; commit/rollback/session cleanup")
    finally:
        await engine.dispose()


def compose(*args):
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "minimew",
            "--file",
            str(ROOT / "compose.yaml"),
            "--env-file",
            str(ROOT / ".env"),
            *args,
        ],
        cwd=ROOT,
        check=True,
        timeout=90,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stop-database", action="store_true", help="短暂停止 minimew postgres 并在 finally 中恢复"
    )
    args = parser.parse_args()
    settings = Settings()
    if (settings.db_host, settings.db_port, settings.db_name, settings.db_user) != (
        "127.0.0.1",
        55432,
        "minimew",
        "minimew",
    ):
        raise SystemExit("This check only supports the default isolated MiniMew local database")
    asyncio.run(check_transactions())
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").status_code == 200
        print("PASS: real PostgreSQL readiness 200")
        if args.stop_database:
            try:
                compose("stop", "postgres")
                assert client.get("/health/live").status_code == 200
                response = client.get("/health/ready")
                assert response.status_code == 503, response.text
                assert response.json() == {"status": "unavailable", "database": "unavailable"}
                print("PASS: database stopped; live 200; ready 503")
            finally:
                compose("up", "-d", "--wait", "postgres")
            assert client.get("/health/ready").status_code == 200
            print("PASS: same app recovered to ready 200")


if __name__ == "__main__":
    main()
