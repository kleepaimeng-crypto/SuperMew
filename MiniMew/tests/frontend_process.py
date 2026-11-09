"""仅在受信任临时库验收中注入可控 Provider；正式路由和执行链保持原样。"""

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
from minimew.execution.provider import ProviderCompleted, ProviderDelta, ProviderFailure
from minimew.settings import Settings


class BrowserProvider:
    async def stream(self, request):
        question = request.messages[-1].content
        yield ProviderDelta("今天")
        await asyncio.sleep(0.15)
        if "取消" in question:
            await asyncio.Event().wait()
        elif "失败" in question:
            raise ProviderFailure("PROVIDER_ERROR")
        else:
            yield ProviderDelta("天气很好")
            yield ProviderCompleted("今天天气很好")


def main():
    settings = Settings()
    assert os.environ.get("MINIMEW_CHAT_BROWSER_TESTS") == "1"
    assert settings.db_name == os.environ["MINIMEW_INTEGRATION_DB"]
    assert settings.db_name.startswith("minimew_test_")
    assert len(settings.db_name.removeprefix("minimew_test_")) == 32
    assert (settings.db_host, settings.db_port) == ("127.0.0.1", 55432)

    import minimew.execution.deepseek as adapter

    adapter.DeepSeekProvider = lambda key: BrowserProvider()

    @asynccontextmanager
    async def journal_only(sessions, settings):
        yield None

    # 这里验收浏览器/真实 Journal，不重复 Redis 集成；仍使用正式超时补查路径。
    app_module.notification_lifespan = journal_only
    owned_socket = socket.socket()
    owned_socket.bind(("127.0.0.1", 0))
    owned_socket.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(
            app_module.create_app(),
            access_log=False,
            log_level="warning",
            ws="none",
            timeout_graceful_shutdown=2,
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
