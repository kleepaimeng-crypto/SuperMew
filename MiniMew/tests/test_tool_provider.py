"""真实LangChain/SDK走内存HTTP传输；不联网、不执行产品工具。"""

import asyncio
import json
from dataclasses import replace

import httpx2
import pytest
from pydantic import SecretStr
from test_deepseek import chunk, request, wire
from test_deepseek import transport as transport
from test_model_messages import search_tool

from minimew.execution import chat_provider as adapter
from minimew.execution.messages import InputMessage, ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted, ProviderFailure


def tool_frame(index=0, *, call_id="call_meal", name="knowledge_search", arguments="{}"):
    frame = chunk()
    function = {"arguments": arguments}
    if name is not None:
        function["name"] = name
    call = {"index": index, "type": "function", "function": function}
    if call_id is not None:
        call["id"] = call_id
    frame["choices"][0]["delta"]["tool_calls"] = [call]
    return frame


def model_request(configuration, messages=None):
    data = request(configuration)
    return replace(data, tools=(search_tool(),), messages=messages or data.messages)


def sse(*frames):
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=wire(*frames)
    )


async def collect(data, provider=None):
    provider = provider or adapter.ChatProvider(SecretStr("test-key"))
    return [event async for event in provider.stream(data)]


def test_executor_sends_first_turn_system_rules_and_preserves_auto_choice(
    transport, model_configuration
):
    from uuid import uuid4

    from test_execution_retry import Repository
    from test_tools import spec

    from minimew.execution.prompts import CHAT_INSTRUCTIONS
    from minimew.execution.service import RunExecutor
    from minimew.execution.tools import ToolRegistry

    async def response(req):
        body = json.loads(req.content)
        assert body["messages"][0]["role"] == "system"
        assert CHAT_INSTRUCTIONS in body["messages"][0]["content"]
        assert '"knowledge_status": "not_searched"' in body["messages"][0]["content"]
        assert body["messages"][-1] == {"role": "user", "content": "now"}
        assert body["tool_choice"] == "auto"
        return sse(chunk("普通回答，未检索。"), chunk(finish="stop"))

    requests, clients = transport(response)

    async def scenario():
        repo = Repository(request(model_configuration))

        async def forbidden(*args):
            raise AssertionError("A direct model answer must not execute tools")

        executor = RunExecutor(
            repo,
            adapter.ChatProvider(SecretStr("test-key")),
            registry=ToolRegistry((spec(forbidden),)),
            capabilities=frozenset({"documents.read"}),
        )
        assert await executor.execute(uuid4(), repo.data.thread_id, repo.data.run_id) == "succeeded"
        repo.record_tool.assert_not_awaited()

    asyncio.run(scenario())
    assert len(requests) == 1 and all(client.is_closed for client in clients)


def test_fragmented_arguments_buffer_entire_turn_and_preserve_call_identity(
    transport, model_configuration
):
    async def response(req):
        body = json.loads(req.content)
        assert str(req.url) == "https://api.deepseek.com/chat/completions"
        assert body["model"] == "deepseek-v4-flash"
        assert body["tools"] == [search_tool().wire()]
        assert body["tool_choice"] == "auto"
        assert "strict" not in body["tools"][0]["function"]
        assert body["thinking"] == {"type": "disabled"}
        return sse(
            chunk("先查询", reasoning="private reasoning"),
            tool_frame(arguments='{"query":"餐'),
            tool_frame(call_id=None, name=None, arguments='费"}'),
            chunk(finish="tool_calls"),
        )

    requests, clients = transport(response)
    result = asyncio.run(collect(model_request(model_configuration)))
    assert result == [
        ProviderCompleted(
            ModelTurn(
                "tool_calls",
                "先查询",
                (ToolCall("call_meal", "knowledge_search", '{"query":"餐费"}'),),
            )
        )
    ]
    assert "private reasoning" not in repr(result)
    assert len(requests) == 1 and all(client.is_closed for client in clients)


def test_interleaved_same_name_calls_and_tool_results_round_trip(transport, model_configuration):
    async def response(req):
        body = json.loads(req.content)
        if len(body["messages"]) == 1:
            return sse(
                tool_frame(0, arguments='{"query":"餐'),
                tool_frame(1, call_id="call_hotel", arguments='{"query":"住'),
                tool_frame(1, call_id=None, name=None, arguments='宿"}'),
                tool_frame(0, call_id=None, name=None, arguments='费"}'),
                chunk(finish="tool_calls"),
            )
        messages = body["messages"]
        calls = messages[1]["tool_calls"]
        assert [call["id"] for call in calls] == ["call_meal", "call_hotel"]
        assert [json.loads(call["function"]["arguments"]) for call in calls] == [
            {"query": "餐费"},
            {"query": "住宿"},
        ]
        assert [(m["role"], m["tool_call_id"], m["content"]) for m in messages[2:]] == [
            ("tool", "call_hotel", "住宿证据"),
            ("tool", "call_meal", "餐费证据"),
        ]
        return sse(chunk("最终"), chunk("回答"), chunk(finish="stop"))

    requests, clients = transport(response)

    async def scenario():
        user = InputMessage("user", "差旅标准？")
        data = model_request(model_configuration, (user,))
        first = (await collect(data))[0].turn
        messages = (
            user,
            InputMessage("assistant", first.content, first.tool_calls),
            InputMessage("tool", "住宿证据", tool_call_id="call_hotel"),
            InputMessage("tool", "餐费证据", tool_call_id="call_meal"),
        )
        assert await collect(replace(data, messages=messages)) == [
            ProviderCompleted(ModelTurn("text", "最终回答"))
        ]
        assert len(requests) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "frames",
    [
        [tool_frame(arguments='{"query":'), chunk(finish="tool_calls")],
        [tool_frame(arguments="{}{}"), chunk(finish="tool_calls")],
        [tool_frame(arguments='{"query":1,"query":2}'), chunk(finish="tool_calls")],
        [tool_frame(arguments='{"query":NaN}'), chunk(finish="tool_calls")],
        [tool_frame(call_id=None), chunk(finish="tool_calls")],
        [tool_frame(name=None), chunk(finish="tool_calls")],
        [tool_frame(name="unregistered"), chunk(finish="tool_calls")],
        [tool_frame(), tool_frame(1), chunk(finish="tool_calls")],
        [tool_frame(1), chunk(finish="tool_calls")],
        [tool_frame(8), chunk(finish="tool_calls")],
        [tool_frame(), tool_frame(call_id="changed"), chunk(finish="tool_calls")],
        [tool_frame(), tool_frame(name="changed"), chunk(finish="tool_calls")],
        [tool_frame(arguments="x" * 16001), chunk(finish="tool_calls")],
        [
            tool_frame(arguments="x" * 8001),
            tool_frame(call_id=None, name=None, arguments="x" * 8000),
            chunk(finish="tool_calls"),
        ],
        [tool_frame(), chunk(finish="stop")],
        [chunk(finish="tool_calls")],
        [tool_frame()],
        [tool_frame(), chunk(finish="tool_calls"), chunk("late")],
    ],
    ids=[
        "partial",
        "trailing",
        "duplicate-key",
        "nan",
        "no-id",
        "no-name",
        "unknown",
        "duplicate-id",
        "gap",
        "too-many",
        "changed-id",
        "changed-name",
        "large-frame",
        "large-total",
        "wrong-finish",
        "no-calls",
        "no-finish",
        "after-finish",
    ],
)
def test_invalid_tool_protocol_emits_no_public_or_completed_event(
    transport, model_configuration, frames
):
    async def response(req):
        return sse(chunk("中间文字"), *frames)

    requests, clients = transport(response)

    async def scenario():
        events = []
        with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_RESPONSE") as failure:
            async for event in adapter.ChatProvider(SecretStr("test-key")).stream(
                model_request(model_configuration)
            ):
                events.append(event)
        assert events == [] and not failure.value.retryable
        assert len(requests) == 1 and all(client.is_closed for client in clients)

    asyncio.run(scenario())


def test_concurrent_tool_requests_do_not_share_fragments(transport, model_configuration):
    async def response(req):
        name = json.loads(req.content)["messages"][0]["content"]
        await asyncio.sleep(0)
        return sse(
            tool_frame(call_id=name, arguments=json.dumps({"query": name})),
            chunk(finish="tool_calls"),
        )

    requests, clients = transport(response)

    async def scenario():
        provider = adapter.ChatProvider(SecretStr("test-key"))
        first, second = await asyncio.gather(
            *(
                collect(model_request(model_configuration, (InputMessage("user", name),)), provider)
                for name in ("A", "B")
            )
        )
        assert first[0].turn.tool_calls == (ToolCall("A", "knowledge_search", '{"query": "A"}'),)
        assert second[0].turn.tool_calls == (ToolCall("B", "knowledge_search", '{"query": "B"}'),)
        assert len(requests) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())


def test_cancellation_during_partial_tool_stream_closes_resources(transport, model_configuration):
    async def scenario():
        partial, closed = asyncio.Event(), asyncio.Event()

        class WaitingStream(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield wire(tool_frame(arguments='{"query":')).split(b"data: [DONE]")[0]
                partial.set()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        async def response(req):
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=WaitingStream()
            )

        requests, clients = transport(response)
        task = asyncio.create_task(collect(model_request(model_configuration)))
        await asyncio.wait_for(partial.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (
            closed.is_set() and len(requests) == 1 and all(client.is_closed for client in clients)
        )

    asyncio.run(scenario())
