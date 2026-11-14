"""check_hitl.py 的受控 API 子进程；只允许显式临时数据库。"""

import json
import os
import socket
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

import minimew.app as app_module
import minimew.execution.deepseek as answer_module
from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted
from minimew.settings import Settings


class ControlledHitl:
    def __init__(self, phase):
        self.phase = phase
        self.calls = []

    async def stream(self, request):
        self.calls.append(request.purpose)
        if request.purpose == "grade":
            data = json.loads(request.messages[-1].content)
            yield ProviderCompleted(
                ModelTurn(
                    "text",
                    json.dumps(
                        {
                            "status": "ambiguous" if self.phase == "pause" else "sufficient",
                            "supported": [m["citation_id"] for m in data["materials"]],
                            "missing": ["国内还是海外？"] if self.phase == "pause" else [],
                        }
                    ),
                )
            )
        elif request.purpose == "answer" and request.messages[-1].role != "tool":
            assert self.phase == "pause", "Resume must not repeat original model decision"
            yield ProviderCompleted(
                ModelTurn(
                    "tool_calls",
                    "",
                    (ToolCall("meal", "knowledge_search", '{"query":"培训餐费报销"}'),),
                )
            )
        else:
            assert request.purpose == "answer" and self.phase == "resume"
            result = json.loads(request.messages[-1].content)
            match = result["matches"][0]
            yield ProviderCompleted(
                ModelTurn("text", f"## 文档依据\n{match['text']}[{match['citation_id']}]")
            )


def main():
    assert os.environ.get("MINIMEW_HITL_CHECK") == "1"
    assert Settings().db_name.startswith("minimew_test_")
    controlled = ControlledHitl(sys.argv[2])
    answer_module.DeepSeekProvider = lambda key: None if sys.argv[2] == "accept" else controlled
    # 只验收 Journal，不创建 Redis 通知键。
    from check_knowledge import offline_notifications

    app_module.notification_lifespan = offline_notifications
    owned = socket.socket()
    owned.bind(("127.0.0.1", 0))
    owned.listen(128)
    app = app_module.create_app()
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def controlled_lifespan(application):
        async with lifespan(application):
            # Windows DLL 首次加载期间不启动阻塞 stdin 的控制线程。
            threading.Thread(target=stop, daemon=True).start()
            yield

    app.router.lifespan_context = controlled_lifespan
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="info",
            access_log=False,
            ws="none",
            timeout_graceful_shutdown=3,
        )
    )

    def stop():
        sys.stdin.readline()
        server.should_exit = True

    Path(sys.argv[1]).write_text(json.dumps({"port": owned.getsockname()[1]}))
    try:
        server.run(sockets=[owned])
    finally:
        owned.close()
        Path(sys.argv[1]).with_suffix(".calls.json").write_text(json.dumps(controlled.calls))


if __name__ == "__main__":
    main()
