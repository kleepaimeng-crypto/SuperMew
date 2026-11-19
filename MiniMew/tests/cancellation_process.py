"""仅供显式临时库验收启动的进程；通过 stdin 正常停止，不开放测试 HTTP 路由。"""

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
from minimew.execution.provider import ProviderDelta
from minimew.settings import Settings


class WaitingProvider:
    async def stream(self, request):
        yield ProviderDelta("process-prefix")
        await asyncio.Event().wait()


def main():
    assert os.environ.get("MINIMEW_CANCELLATION_TESTS") == "1"
    settings = Settings()
    assert settings.db_name == os.environ["MINIMEW_INTEGRATION_DB"]
    assert settings.db_name.startswith("minimew_test_")
    assert len(settings.db_name.removeprefix("minimew_test_")) == 32
    if settings.model_credentials_available:
        import minimew.execution.chat_provider as adapter

        adapter.ChatProvider = lambda key, **kwargs: WaitingProvider()

    @asynccontextmanager
    async def offline_notifications(sessions, settings):
        yield None

    app_module.notification_lifespan = offline_notifications
    owned_socket = socket.socket()
    owned_socket.bind(("127.0.0.1", 0))
    owned_socket.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(
            app_module.create_app(),
            log_level="warning",
            access_log=False,
            ws="none",
            lifespan="on",
            timeout_graceful_shutdown=1,
        )
    )

    def stop():
        sys.stdin.readline()
        server.should_exit = True

    threading.Thread(target=stop, daemon=True).start()
    Path(sys.argv[1]).write_text(json.dumps({"port": owned_socket.getsockname()[1]}))
    try:
        server.run(sockets=[owned_socket])
    finally:
        owned_socket.close()


if __name__ == "__main__":
    main()
