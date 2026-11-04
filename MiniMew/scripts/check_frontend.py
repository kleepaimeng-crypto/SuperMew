"""显式真实浏览器验收：独占临时库与后端进程，不修改开发数据。"""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from check_models import ROOT, migrate
from sqlalchemy import text

from minimew.database import create_engine
from minimew.settings import Settings


async def main():
    settings = Settings()
    if (settings.db_host, settings.db_port, settings.db_name, settings.db_user) != (
        "127.0.0.1",
        55432,
        "minimew",
        "minimew",
    ):
        raise SystemExit("Only the default local MiniMew database server is supported")
    for port in (8002, 5175):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is occupied; no existing process will be stopped")
    database = "minimew_test_" + uuid4().hex
    engine = create_engine(settings)
    created = False
    backend = None
    log_path = ROOT / ".artifacts" / "frontend-test-backend.log"
    log_path.parent.mkdir(exist_ok=True)
    backend_log = log_path.open("w", encoding="utf-8")
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        migrate(database, "upgrade", "head")
        env = dict(os.environ, MINIMEW_DB_NAME=database)
        backend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "minimew.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8002",
            ],
            cwd=ROOT,
            env=env,
            stdout=backend_log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("Temporary backend exited before readiness")
            try:
                if httpx.get("http://127.0.0.1:8002/health/ready", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Temporary backend readiness timed out")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise RuntimeError("npm is required")
        subprocess.run(
            [npm, "run", "test:e2e"],
            cwd=ROOT / "frontend",
            env=dict(os.environ, MINIMEW_LIVE_API="1", MINIMEW_API_TARGET="http://127.0.0.1:8002"),
            check=True,
            timeout=120,
        )
    finally:
        if backend is not None:
            backend.terminate()
            backend.wait(timeout=10)
        backend_log.close()
        if created:
            async with engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f'DROP DATABASE "{database}"'))
            print("Cleaned up owned browser-test database and backend")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
