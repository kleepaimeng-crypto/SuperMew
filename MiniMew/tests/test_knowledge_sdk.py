"""知识工具的真实LangChain/SDK wire接线；检索/HTTP均受控，不联网。"""

import asyncio
import json

import httpx2
from pydantic import SecretStr
from test_deepseek import chunk, request, wire
from test_deepseek import transport as transport
from test_execution_retry import Repository
from test_knowledge import setup_service
from test_tool_provider import tool_frame

from minimew.execution.deepseek import DeepSeekProvider
from minimew.execution.service import RunExecutor


def test_sdk_receives_knowledge_schema_and_paired_original_material(
    transport, model_configuration, monkeypatch
):
    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)

    async def response(req):
        body = json.loads(req.content)
        tool = body["tools"][0]["function"]
        assert tool["name"] == "knowledge_search"
        assert set(tool["parameters"]["properties"]) == {"query"}
        assert body["thinking"] == {"type": "disabled"}
        if body["messages"][-1]["role"] == "user":
            frames = [tool_frame(arguments='{"query":"餐费"}'), chunk(finish="tool_calls")]
        else:
            message = body["messages"][-1]
            assert message["tool_call_id"] == "call_meal"
            value = json.loads(message["content"])
            assert value["status"] == "ok" and "150" in value["matches"][0]["text"]
            assert "embedding_text" not in message["content"]
            frames = [chunk("依据工具材料回答"), chunk(finish="stop")]
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=wire(*frames)
        )

    requests, clients = transport(response)

    async def scenario():
        repo = Repository(request(model_configuration))
        executor = RunExecutor(
            repo,
            DeepSeekProvider(SecretStr("test-key")),
            registry=registry,
            capabilities=context.capabilities,
        )
        assert (
            await executor.execute(context.user_id, repo.data.thread_id, repo.data.run_id)
            == "succeeded"
        )
        assert len(requests) == 2 and all(client.is_closed for client in clients)
        assert repo.finish.await_args.kwargs["content"] == "依据工具材料回答"
        repo.append_delta.assert_not_awaited()
        scoped.assert_awaited_once()
        search.assert_awaited_once()

    asyncio.run(scenario())
