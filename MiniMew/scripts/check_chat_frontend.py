"""浏览器 + 真实 API/PG + 受控 Provider；自有临时库/端口/进程，不调用真实模型。"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from uuid import uuid4

import httpx
from check_models import ROOT
from check_runtime import temporary_database
from fixture_models import model_environment

from minimew.model_control import ModelConfiguration


async def main(tests):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 5175))
        except OSError:
            raise SystemExit("Port 5175 is occupied; no existing process will be stopped") from None
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("npm is required")
    folder = ROOT / ".artifacts" / f"chat-browser-{uuid4().hex}"
    folder.mkdir(parents=True)
    async with temporary_database() as database:
        config = ModelConfiguration.model_validate_json(
            (ROOT / "model-profile.deepseek-v4-flash.json").read_text()
        ).model_copy(update={"timeout_seconds": 15.0, "max_output_tokens": 128})
        environment = dict(
            os.environ,
            **model_environment(config, adapter="deepseek"),
            MINIMEW_DB_NAME=database,
            MINIMEW_INTEGRATION_DB=database,
            MINIMEW_CHAT_BROWSER_TESTS="1",
            MINIMEW_KNOWLEDGE_ENABLED="false",
            MINIMEW_DEEPSEEK_API_KEY="test-only-no-network",
            MINIMEW_EVENT_POLL_SECONDS="0.05",
            MINIMEW_EXECUTION_SCAN_SECONDS="0.05",
            MINIMEW_CANCELLATION_POLL_SECONDS="0.05",
        )
        process = None
        with (folder / "backend.log").open("w", encoding="utf-8") as log:
            try:
                info = folder / "address.json"
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "tests" / "frontend_process.py"), str(info)],
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.PIPE,
                    text=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                async with httpx.AsyncClient(trust_env=False) as client:
                    for _ in range(200):
                        if process.poll() is not None:
                            raise RuntimeError(
                                "Owned frontend-test API exited; inspect its local log"
                            )
                        if info.exists():
                            base = f"http://127.0.0.1:{json.loads(info.read_text())['port']}"
                            try:
                                response = await client.get(base + "/health/ready", timeout=1)
                                if response.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                        await asyncio.sleep(0.1)
                    else:
                        raise RuntimeError("Owned frontend-test API readiness timed out")
                await asyncio.to_thread(
                    subprocess.run,
                    [npm, "run", "test:e2e", "--", *tests],
                    cwd=ROOT / "frontend",
                    check=True,
                    timeout=120,
                    env=dict(
                        environment,
                        MINIMEW_CHAT_LIVE="1",
                        MINIMEW_LIVE_API="1",
                        MINIMEW_API_TARGET=base,
                    ),
                )
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.stdin.write("stop\n")
                        process.stdin.flush()
                        try:
                            await asyncio.to_thread(process.wait, timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=10)
                    process.stdin.close()
                    if process.returncode != 0:
                        raise RuntimeError("Owned API did not exit cleanly")
        print("Owned browser API stopped; temporary database cleanup follows")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["tests/chat-live.spec.ts", "tests/live.spec.ts"]))
