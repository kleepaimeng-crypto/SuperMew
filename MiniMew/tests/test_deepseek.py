"""真实 LangChain/SDK + 内存 HTTP transport；默认测试不访问外部网络。"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from langsmith.run_helpers import get_tracing_context
from pydantic import SecretStr

import minimew.execution.deepseek as adapter
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import (
    InputMessage,
    ProviderCompleted,
    ProviderDelta,
    ProviderFailure,
    ProviderRequest,
)
from minimew.model_control import ModelSnapshot, ProfileSnapshot


def request(configuration):
    configuration = configuration.model_copy(
        update={
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-v4-flash",
        }
    )
    return ProviderRequest(
        uuid4(),
        uuid4(),
        ModelSnapshot(
            answer=ProfileSnapshot(
                profile_id=uuid4(),
                profile_version=1,
                configuration=configuration,
            )
        ),
        (
            InputMessage("user", "first"),
            InputMessage("assistant", "prior"),
            InputMessage("user", "now"),
        ),
    )


def wire(*chunks):
    return b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks) + (
        b"data: [DONE]\n\n"
    )


def chunk(text=None, *, finish=None, reasoning=None):
    delta = {"content": text}
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {
        "id": "test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


@pytest.fixture
def transport(monkeypatch):
    requests, clients = [], []

    def install(handler):
        async def dispatch(req):
            requests.append(req)
            assert get_tracing_context()["enabled"] is False
            return await handler(req)

        def async_client(**kwargs):
            client = httpx2.AsyncClient(transport=httpx2.MockTransport(dispatch), **kwargs)
            clients.append(client)
            return client

        def sync_client(**kwargs):
            client = httpx2.Client(**kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(
            adapter,
            "httpx2",
            SimpleNamespace(
                AsyncClient=async_client,
                Client=sync_client,
            ),
        )
        return requests, clients

    return install


def test_sdk_parameters_public_stream_and_close(transport, model_configuration, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-inherited-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "wrong-inherited-key")
    monkeypatch.setenv("OPENAI_ORG_ID", "private-inherited-org")

    async def response(req):
        body = json.loads(req.content)
        assert str(req.url) == "https://api.deepseek.com/chat/completions"
        assert req.headers["authorization"] == "Bearer local-test-key"
        assert "private-inherited-org" not in str(req.headers)
        assert body["model"] == "deepseek-v4-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["temperature"] == 0.2 and body["max_tokens"] == 1024
        assert body["stream"] is True and body["n"] == 1
        assert [m["content"] for m in body["messages"]] == ["first", "prior", "now"]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(
                chunk(reasoning="private reasoning"),
                chunk("x" * 5000),
                chunk("答"),
                chunk(finish="stop"),
            ),
        )

    requests, clients = transport(response)

    async def scenario():
        result = [
            e
            async for e in adapter.DeepSeekProvider(SecretStr("local-test-key")).stream(
                request(model_configuration)
            )
        ]
        assert [len(e.content) for e in result if isinstance(e, ProviderDelta)] == [4096, 904, 1]
        assert result[-1] == ProviderCompleted(ModelTurn("text", "x" * 5000 + "答"))
        assert len(requests) == 1 and all(c.is_closed for c in clients)
        assert "private reasoning" not in repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"finish": "length"},
        {"finish": "tool_calls"},
        {"finish": "content_filter"},
        {"finish": "insufficient_system_resource"},
        {"finish": None},
    ],
)
def test_abnormal_or_missing_finish_is_not_success(transport, model_configuration, changes):
    async def response(req):
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(
                chunk("partial"),
                chunk(**changes),
            ),
        )

    requests, clients = transport(response)

    async def scenario():
        events = []
        with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_RESPONSE"):
            async for event in adapter.DeepSeekProvider(SecretStr("key")).stream(
                request(model_configuration)
            ):
                events.append(event)
        assert events == [ProviderDelta("partial")]
        assert len(requests) == 1 and all(c.is_closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"choices": []},
        {"choices": [{"index": 0, "delta": None, "finish_reason": "stop"}]},
        {"choices": [{"index": 0, "delta": {"content": 123}, "finish_reason": "stop"}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0}]}}]},
        {"choices": [{"index": 1, "delta": {"content": "other"}, "finish_reason": "stop"}]},
    ],
)
def test_empty_and_malformed_protocol_fails(transport, model_configuration, change):
    async def response(req):
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=wire(chunk() | change)
        )

    _, clients = transport(response)

    async def scenario():
        with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_RESPONSE"):
            _ = [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    request(model_configuration)
                )
            ]
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("status,retryable", [(401, False), (400, False), (429, True), (503, True)])
def test_status_errors_are_typed_without_sdk_retry(
    transport, model_configuration, status, retryable
):
    async def response(req):
        return httpx2.Response(
            status,
            headers={"retry-after": "2"},
            json={"error": {"message": "private upstream error", "type": "error"}},
        )

    requests, clients = transport(response)

    async def scenario():
        with pytest.raises(ProviderFailure) as error:
            _ = [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    request(model_configuration)
                )
            ]
        assert error.value.retryable is retryable and error.value.retry_after == 2
        assert str(error.value) == "PROVIDER_ERROR"
        assert len(requests) == 1 and all(c.is_closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "base",
    [
        "https://elsewhere.invalid",
        "http://api.deepseek.com",
        "https://api.deepseek.com:444",
        "https://api.deepseek.com/other",
    ],
)
def test_key_never_sent_to_unapproved_origin(transport, model_configuration, base):
    async def forbidden(req):
        raise AssertionError("Must not make a request")

    requests, clients = transport(forbidden)

    async def scenario():
        data = request(model_configuration)
        snapshot = data.snapshot.model_copy(
            update={
                "answer": data.snapshot.answer.model_copy(
                    update={
                        "configuration": data.snapshot.answer.configuration.model_copy(
                            update={"base_url": base}
                        )
                    }
                )
            }
        )
        with pytest.raises(ProviderFailure):
            _ = [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    replace(data, snapshot=snapshot)
                )
            ]
        assert not requests and not clients

    asyncio.run(scenario())


def test_two_calls_keep_inputs_and_clients_separate(transport, model_configuration):
    async def response(req):
        text = json.loads(req.content)["messages"][-1]["content"]
        await asyncio.sleep(0)
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk(text), chunk(finish="stop")),
        )

    requests, clients = transport(response)

    async def scenario():
        provider = adapter.DeepSeekProvider(SecretStr("key"))

        async def collect(text):
            data = replace(request(model_configuration), messages=(InputMessage("user", text),))
            return [e async for e in provider.stream(data)]

        first, second = await asyncio.gather(collect("A"), collect("B"))
        assert first == [ProviderDelta("A"), ProviderCompleted(ModelTurn("text", "A"))]
        assert second == [ProviderDelta("B"), ProviderCompleted(ModelTurn("text", "B"))]
        assert len(requests) == 2 and len(clients) == 4 and all(c.is_closed for c in clients)

    asyncio.run(scenario())


def test_cancel_closes_clients(transport, model_configuration):
    async def scenario():
        started = asyncio.Event()

        async def response(req):
            started.set()
            await asyncio.Event().wait()

        _, clients = transport(response)

        async def collect():
            return [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    request(model_configuration)
                )
            ]

        task = asyncio.create_task(collect())
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())


def test_initialization_error_closes_partial_resources(transport, model_configuration, monkeypatch):
    async def response(req):
        raise AssertionError("Initialization must fail before request")

    requests, clients = transport(response)

    def broken(**kwargs):
        raise ValueError("private initialization error")

    monkeypatch.setattr(adapter, "_AnswerChatDeepSeek", broken)

    async def scenario():
        with pytest.raises(ProviderFailure):
            _ = [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    request(model_configuration)
                )
            ]
        assert not requests and len(clients) == 2 and all(c.is_closed for c in clients)

    asyncio.run(scenario())


def test_redirect_cannot_receive_key(transport, model_configuration):
    async def response(req):
        assert req.url.host == "api.deepseek.com"
        return httpx2.Response(307, headers={"location": "https://elsewhere.invalid"})

    requests, clients = transport(response)

    async def scenario():
        with pytest.raises(ProviderFailure) as error:
            _ = [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    request(model_configuration)
                )
            ]
        assert not error.value.retryable
        assert len(requests) == 1 and all(c.is_closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [False, True])
def test_stream_network_error_is_transient_without_sdk_retry(
    transport,
    model_configuration,
    timeout,
):
    class BrokenStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            if timeout:
                raise httpx2.ReadTimeout("private network details")
            raise httpx2.ReadError("private network details")
            yield

    async def response(req):
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=BrokenStream()
        )

    requests, clients = transport(response)

    async def scenario():
        with pytest.raises(ProviderFailure) as error:
            _ = [
                e
                async for e in adapter.DeepSeekProvider(SecretStr("key")).stream(
                    request(model_configuration)
                )
            ]
        assert error.value.retryable
        assert error.value.code == ("PROVIDER_TIMEOUT" if timeout else "PROVIDER_ERROR")
        assert len(requests) == 1 and all(c.is_closed for c in clients)

    asyncio.run(scenario())
