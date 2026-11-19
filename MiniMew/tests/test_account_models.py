import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from test_deepseek import chunk, request, wire
from test_deepseek import transport as transport
from test_tool_provider import tool_frame

from minimew.account_models.__main__ import initialize
from minimew.account_models.credentials import decrypt, encrypt
from minimew.account_models.network import PublicTransport, public_url
from minimew.account_models.schemas import ModelWrite
from minimew.execution.chat_provider import ChatProvider
from minimew.execution.messages import InputMessage
from minimew.model_control import ModelConfigurationUnavailable


def settings():
    return SimpleNamespace(credential_master_key=SecretStr(Fernet.generate_key().decode()))


def test_authenticated_encryption_binding_and_explicit_key_initialization(tmp_path):
    config = settings()
    owner, identity = uuid4(), uuid4()
    url = "https://api.deepseek.com"
    ciphertext = encrypt(config, owner, identity, url, "unit-private-key")
    item = SimpleNamespace(user_id=owner, id=identity, encrypted_value=ciphertext)
    assert "unit-private-key" not in ciphertext
    assert decrypt(config, item, url).get_secret_value() == "unit-private-key"
    for changed, address, key_settings in (
        (SimpleNamespace(**(item.__dict__ | {"user_id": uuid4()})), url, config),
        (SimpleNamespace(**(item.__dict__ | {"id": uuid4()})), url, config),
        (item, "https://other.example.com", config),
        (item, url, settings()),
    ):
        with pytest.raises(ModelConfigurationUnavailable):
            decrypt(key_settings, changed, address)
    path = tmp_path / ".env"
    path.write_text("MINIMEW_APP_NAME=Example\n", encoding="utf-8")
    assert initialize(path)
    first = path.read_bytes()
    assert not initialize(path) and path.read_bytes() == first
    assert "MINIMEW_APP_NAME=Example" in path.read_text()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://169.254.169.254",
        "https://localhost",
        "https://host.local",
        "https://x.com:8443",
        "https://key@example.com",
        "https://example.com/?key=secret",
    ],
)
def test_custom_endpoints_reject_nonpublic_or_credential_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_dns_is_validated_and_connection_pinned_with_original_tls_name(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "minimew.account_models.network.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )

    async def scenario():
        transport = PublicTransport()

        async def accept(req):
            seen.append(req)
            return httpx2.Response(200, content=b"ok")

        transport.inner = httpx2.MockTransport(accept)
        request = httpx2.Request("GET", "https://provider.example.com/v1/models")
        await transport.handle_async_request(request)
        assert seen[0].url.host == "8.8.8.8"
        assert seen[0].headers["host"] == "provider.example.com"
        assert seen[0].extensions["sni_hostname"] == "provider.example.com"
        monkeypatch.setattr(
            "minimew.account_models.network.socket.getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
        )
        with pytest.raises(httpx2.ConnectError):
            await transport.handle_async_request(request)
        assert len(seen) == 1
        await transport.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "family,model",
    [
        ("deepseek", "deepseek-flash"),
        ("qwen", "qwen3.8-flash"),
        ("kimi", "kimi-k3.1-pro"),
        ("generic", "my-model"),
    ],
)
def test_presets_and_generic_share_one_adapter_with_distinct_request_parameters(
    family,
    model,
    transport,
    model_configuration,
):
    write = ModelWrite(
        name="模型",
        adapter=family,
        model_name=model,
        base_url="https://provider.example.com/v1" if family == "generic" else None,
    )
    configuration = write.configuration()

    async def response(req):
        import json

        body = json.loads(req.content)
        assert body["model"] == model
        if family == "deepseek":
            assert body["thinking"] == {"type": "disabled"}
        elif family == "qwen":
            assert body["enable_thinking"] is False and "thinking" not in body
        else:
            assert "thinking" not in body and "enable_thinking" not in body
        if family == "kimi":
            assert "temperature" not in body and "max_completion_tokens" in body
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk("回答"), chunk(finish="stop")),
        )

    calls, clients = transport(response)
    data = request(model_configuration)
    data = replace(
        data,
        snapshot=data.snapshot.model_copy(
            update={
                "answer": data.snapshot.answer.model_copy(update={"configuration": configuration})
            }
        ),
    )

    async def collect():
        return [event async for event in ChatProvider(SecretStr("unit-key")).stream(data)]

    result = asyncio.run(collect())
    assert result[-1].turn.content == "回答" and len(calls) == 1
    assert all(c.is_closed for c in clients)


def test_kimi_private_continuation_round_trips_without_becoming_public_output(
    transport, model_configuration
):
    import json

    from test_model_messages import search_tool

    configuration = ModelWrite(name="Kimi", adapter="kimi", model_name="kimi-k3").configuration()
    data = request(model_configuration)
    data = replace(
        data,
        tools=(search_tool(),),
        messages=(InputMessage("user", "查资料"),),
        snapshot=data.snapshot.model_copy(
            update={
                "answer": data.snapshot.answer.model_copy(update={"configuration": configuration})
            }
        ),
    )

    async def response(req):
        body = json.loads(req.content)
        if len(body["messages"]) == 1:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=wire(
                    chunk(reasoning="private-kimi-continuation"),
                    tool_frame(arguments='{"query":"资料"}'),
                    chunk(finish="tool_calls"),
                ),
            )
        assert body["messages"][1]["reasoning_content"] == "private-kimi-continuation"
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk("已找到"), chunk(finish="stop")),
        )

    calls, _ = transport(response)

    async def scenario():
        provider = ChatProvider(SecretStr("unit-key"))
        first = [e async for e in provider.stream(data)][0].turn
        assert first.content == "" and "private-kimi" not in repr(first)
        assistant = InputMessage(
            "assistant", first.content, first.tool_calls, reasoning_content=first.reasoning_content
        )
        from minimew.hitl.checkpoint import decode, encode

        assert decode(encode(assistant)) == assistant
        second = replace(
            data,
            messages=(
                *data.messages,
                assistant,
                InputMessage("tool", "资料结果", tool_call_id=first.tool_calls[0].id),
            ),
        )
        result = [e async for e in provider.stream(second)]
        assert result[-1].turn.content == "已找到" and "private-kimi" not in repr(result)

    asyncio.run(scenario())
    assert len(calls) == 2
