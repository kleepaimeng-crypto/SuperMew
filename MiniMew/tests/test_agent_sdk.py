"""真实SDK两轮接线，HTTP完全在内存中，不访问DeepSeek。"""

import asyncio
import json

import httpx2
from pydantic import SecretStr
from test_agent import executor, run
from test_deepseek import chunk, request, wire
from test_deepseek import transport as transport
from test_execution_retry import Repository
from test_tool_provider import tool_frame
from test_tools import Result

from minimew.execution.chat_provider import ChatProvider


def test_real_sdk_agent_calls_tool_then_sends_paired_result_to_model(
    transport, model_configuration
):
    async def response(req):
        body = json.loads(req.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["tools"][0]["function"]["parameters"]["additionalProperties"] is False
        if body["messages"][-1]["role"] == "user":
            frames = [
                tool_frame(name="search", arguments='{"query":"餐费"}'),
                chunk(finish="tool_calls"),
            ]
        else:
            assert body["messages"][-2]["tool_calls"][0]["id"] == "call_meal"
            assert body["messages"][-1]["tool_call_id"] == "call_meal"
            assert json.loads(body["messages"][-1]["content"]) == {"text": "受控证据"}
            frames = [chunk("最终答案"), chunk(finish="stop")]
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=wire(*frames)
        )

    requests, clients = transport(response)

    async def scenario():
        repo = Repository(request(model_configuration))
        invoked = []

        async def handler(context, arguments):
            invoked.append((context.run_id, arguments.query))
            return Result(text="受控证据")

        assert (
            await run(executor(repo, ChatProvider(SecretStr("test-key")), handler), repo)
            == "succeeded"
        )
        assert invoked == [(repo.data.run_id, "餐费")]
        repo.append_delta.assert_not_awaited()
        assert repo.finish.await_args.kwargs == {"content": "最终答案", "error_code": None}
        assert len(requests) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())
