"""仅在受信任临时库验收中注入可控 Provider；正式路由和执行链保持原样。"""

import asyncio
import json
import os
import re
import socket
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

import minimew.app as app_module
from minimew.execution.messages import ModelTurn, ToolCall
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
            yield ProviderCompleted(ModelTurn("text", "今天天气很好"))


class KnowledgeBrowserProvider:
    """仅验收协议与界面，知识检索/CPU/Milvus仍由正式资源执行。"""

    async def stream(self, request):
        question = next(m.content for m in reversed(request.messages) if m.role == "user")
        if request.messages[-1].role != "tool":
            yield ProviderCompleted(
                ModelTurn(
                    "tool_calls",
                    "",
                    (ToolCall("meal", "knowledge_search", '{"query":"餐费报销上限"}'),),
                )
            )
            return
        if "取消" in question:
            await asyncio.Event().wait()
        if "模型故障" in question:
            raise ProviderFailure("PROVIDER_ERROR")
        result = json.loads(request.messages[-1].content)
        if result["matches"]:
            first = result["matches"][0]
            body = "\n\n".join(
                f"{p}[{first['citation_id']}]" for p in re.split(r"\n\s*\n", first["text"].strip())
            )
            text = (
                f"## 文档依据\n{body}\n\n## 模型补充（未由本次资料验证）\n"
                "一般建议：提交报销前核对实际凭证。"
            )
        else:
            text = "## 模型补充（未由本次资料验证）\n本次没有文档依据，无法确认公司的报销标准。"
        yield ProviderCompleted(ModelTurn("text", text))


def main():
    settings = Settings()
    assert os.environ.get("MINIMEW_CHAT_BROWSER_TESTS") == "1"
    assert settings.db_name == os.environ["MINIMEW_INTEGRATION_DB"]
    assert settings.db_name.startswith("minimew_test_")
    assert len(settings.db_name.removeprefix("minimew_test_")) == 32
    assert (settings.db_host, settings.db_port) == ("127.0.0.1", 55432)

    import minimew.execution.deepseek as adapter

    knowledge = os.environ.get("MINIMEW_KNOWLEDGE_BROWSER_TESTS") == "1"
    adapter.DeepSeekProvider = lambda key: (
        KnowledgeBrowserProvider() if knowledge else BrowserProvider()
    )

    @asynccontextmanager
    async def journal_only(sessions, settings):
        yield None

    # 这里验收浏览器/真实 Journal，不重复 Redis 集成；仍使用正式超时补查路径。
    app_module.notification_lifespan = journal_only
    owned_socket = socket.socket()
    owned_socket.bind(("127.0.0.1", 0))
    owned_socket.listen(128)
    app = app_module.create_app()
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def controlled_lifespan(application):
        async with lifespan(application):
            # 本地模型扩展预热后才开始阻塞stdin读取，避免Windows导入锁问题。
            threading.Thread(target=stop, daemon=True).start()
            yield

    app.router.lifespan_context = controlled_lifespan
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            access_log=False,
            log_level="warning",
            ws="none",
            timeout_graceful_shutdown=2,
        )
    )

    def stop():
        sys.stdin.readline()
        server.should_exit = True

    Path(sys.argv[1]).write_text(json.dumps({"port": owned_socket.getsockname()[1]}))
    try:
        server.run(sockets=[owned_socket])
    finally:
        owned_socket.close()


if __name__ == "__main__":
    main()
