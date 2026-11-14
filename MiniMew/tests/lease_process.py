"""租约故障验收的独立 API；无测试 HTTP route、收费调用或非自有进程操作。"""

import asyncio
import json
import os
import socket
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

import minimew.app as app_module
import minimew.execution.deepseek as provider_module
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.repository import ExecutionRepository
from minimew.settings import Settings


def main():
    assert os.environ.get("MINIMEW_LEASE_PROCESS") == "1"
    assert Settings().db_name == os.environ["MINIMEW_INTEGRATION_DB"]
    assert Settings().db_name.startswith("minimew_test_")
    directory, mode, nonce = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    calls = []

    class Controlled:
        async def stream(self, request):
            calls.append(str(request.run_id))
            (directory / "calls.json").write_text(json.dumps(calls))
            if mode == "answer":
                yield ProviderCompleted(ModelTurn("text", "next-answer"))
                return
            yield ProviderDelta("prefix")
            if mode == "block":
                await asyncio.Event().wait()
            yield ProviderCompleted(ModelTurn("text", "late-answer"))

    provider_module.DeepSeekProvider = lambda key: Controlled()
    if mode == "finish_wait":
        original = ExecutionRepository.finish

        async def delayed(self, claim, **kwargs):
            if kwargs.get("error_code") is None:
                (directory / "finish-ready.json").write_text(json.dumps({"run": str(claim.run_id)}))
                while not (directory / "release").exists():
                    await asyncio.sleep(0.01)
            return await original(self, claim, **kwargs)

        ExecutionRepository.finish = delayed

    @asynccontextmanager
    async def offline_notifications(sessions, settings):
        yield None

    app_module.notification_lifespan = offline_notifications
    app = app_module.create_app()
    lifetime = app.router.lifespan_context
    owned = socket.socket()
    owned.bind(("127.0.0.1", 0))
    owned.listen(128)

    @asynccontextmanager
    async def lifespan(application):
        async with lifetime(application):
            threading.Thread(target=stop, daemon=True).start()
            yield

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(
        uvicorn.Config(
            app, log_level="warning", access_log=False, ws="none", timeout_graceful_shutdown=1
        )
    )

    def stop():
        sys.stdin.readline()
        server.should_exit = True

    (directory / "ready.json").write_text(
        json.dumps({"pid": os.getpid(), "port": owned.getsockname()[1], "nonce": nonce})
    )
    try:
        server.run(sockets=[owned])
    finally:
        owned.close()


if __name__ == "__main__":
    main()
