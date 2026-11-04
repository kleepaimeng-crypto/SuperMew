"""Run 定向集成验收：独立临时库；不重复历史迁移回归，不改开发数据。"""

import argparse
import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

from check_models import ROOT, migrate
from sqlalchemy import text

from minimew.database import create_engine
from minimew.settings import Settings


@asynccontextmanager
async def temporary_database(*, revision="head"):
    settings = Settings()
    if (settings.db_host, settings.db_port, settings.db_name, settings.db_user) != (
        "127.0.0.1",
        55432,
        "minimew",
        "minimew",
    ):
        raise SystemExit("Only the default local MiniMew database server is supported")
    database = "minimew_test_" + uuid4().hex
    engine = create_engine(settings)
    created = False
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        migrate(database, "upgrade", revision)
        yield database
    finally:
        try:
            if created:
                async with engine.connect() as connection:
                    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(text(f'DROP DATABASE "{database}"'))
                print("Cleaned up owned temporary runtime database")
        finally:
            await engine.dispose()


async def main(tests):
    async with temporary_database() as database:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", *tests],
            cwd=ROOT,
            check=True,
            timeout=180,
            env=dict(os.environ, MINIMEW_INTEGRATION_DB=database, MINIMEW_NOTIFICATION_TESTS="1"),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tests",
        nargs="*",
        default=[
            "tests/test_runtime_pg.py",
            "tests/test_runs_pg.py",
            "tests/test_execution_pg.py",
            "tests/test_events_pg.py",
        ],
    )
    asyncio.run(main(parser.parse_args().tests))
