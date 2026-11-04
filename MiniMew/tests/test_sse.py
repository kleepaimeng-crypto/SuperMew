"""传输协议与响应生命周期测试；不依赖网络、数据库或真实 Provider。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from minimew.app import create_app
from minimew.conversation_schemas import RunEventPage
from minimew.database import get_session
from minimew.execution.queries import EventRead
from minimew.execution.streaming import (
    HEARTBEAT,
    EventStreamResponse,
    EventSubscription,
    StreamOptions,
    encode_event,
)
from minimew.generated.run_event_v1 import EVENT_ADAPTER
from minimew.security import issue_access_token
from minimew.settings import Settings


def event(sequence, kind="message.delta"):
    data = {"message_id": str(uuid4()), "content": "你好\n\n下一行"}
    if kind == "run.completed":
        data = {"status": "succeeded"}
    return EVENT_ADAPTER.validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": str(uuid4()),
                "run_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "sequence": sequence,
                "type": kind,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": data,
            }
        )
    )


def reading(items=(), *, status="running", last=0, after=0, more=False):
    return EventRead(
        RunEventPage(
            items=list(items), next_cursor=items[-1].sequence if items else after, has_more=more
        ),
        status,
        last,
    )


def subscription(*, expires=None, **options):
    return EventSubscription(
        MagicMock(),
        uuid4(),
        uuid4(),
        uuid4(),
        after=0,
        expires_at=expires or datetime.now(UTC) + timedelta(minutes=1),
        options=StreamOptions(**options),
    )


def test_event_frame_preserves_json_unicode_and_escapes_embedded_frame_delimiters():
    original = event(3)
    frame = encode_event(original)
    lines = frame.decode().splitlines()
    assert lines[0] == "event: message.delta" and len(lines) == 3
    assert EVENT_ADAPTER.validate_json(lines[1].removeprefix("data: ")) == original
    assert not any(line.startswith("id:") for line in lines)
    assert HEARTBEAT.startswith(b":") and HEARTBEAT.endswith(b"\n\n")


@pytest.mark.parametrize(
    "name,value",
    [
        ("poll_interval", 0),
        ("poll_interval", True),
        ("heartbeat_interval", float("nan")),
        ("send_timeout", float("inf")),
        ("max_seconds", -1),
        ("max_seconds", 3601),
        ("page_size", 0),
        ("page_size", 101),
        ("page_size", True),
    ],
)
def test_stream_options_are_bounded(name, value):
    with pytest.raises(ValueError):
        StreamOptions(**{name: value})


def test_terminal_status_drains_backlog_instead_of_ending_at_first_page():
    async def scenario():
        sub = subscription()
        second = reading([event(3), event(4, "run.completed")], status="succeeded", last=4)
        sub.read_page = AsyncMock(return_value=second)
        first = reading([event(1), event(2)], status="succeeded", last=4, more=True)
        frames = [frame async for frame in sub.events(first)]
        assert len(frames) == 4 and sub.cursor == 4
        sub.read_page.assert_awaited_once_with(2)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["disconnect", "expires", "slow-send", "cancel"])
def test_response_closes_generator_and_child_tasks_on_all_connection_endings(reason):
    async def scenario():
        sub = subscription(
            expires=datetime.now(UTC) + timedelta(seconds=0.04 if reason == "expires" else 60),
            send_timeout=0.02,
            max_seconds=1,
        )
        entered, closed = asyncio.Event(), asyncio.Event()
        incoming, sent = asyncio.Queue(), []

        async def body():
            try:
                entered.set()
                yield HEARTBEAT
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("more_body"):
                if reason == "slow-send":
                    await asyncio.Event().wait()
                if reason == "disconnect":
                    incoming.put_nowait({"type": "http.disconnect"})
            sent.append(message)

        response = EventStreamResponse(sub, reading())
        response.body_iterator = body()
        before = asyncio.all_tasks()
        task = asyncio.create_task(response({"type": "http"}, incoming.get, send))
        async with asyncio.timeout(1):
            await entered.wait()
            if reason == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
        assert closed.is_set()
        assert not (asyncio.all_tasks() - before)
        if reason in {"expires", "slow-send"}:
            assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}

    asyncio.run(scenario())


def test_authentication_store_failure_is_controlled_before_stream_headers(monkeypatch):
    import minimew.api.runs as runs

    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-test-only")
    monkeypatch.setattr(
        runs, "get_current_access", AsyncMock(side_effect=SQLAlchemyError("secret"))
    )
    app = create_app()

    async def offline():
        yield MagicMock()

    app.dependency_overrides[get_session] = offline
    token = issue_access_token(uuid4(), Settings())
    with TestClient(app) as client:
        result = client.get(
            f"/v1/threads/{uuid4()}/runs/{uuid4()}/events/stream",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert result.status_code == 503
    assert result.json() == {"detail": {"code": "EVENT_STREAM_UNAVAILABLE"}}
    assert "secret" not in result.text and "text/event-stream" not in result.headers["content-type"]


@pytest.mark.parametrize("before_headers", [True, False])
def test_expiry_is_checked_before_send_even_if_sender_never_suspends(before_headers):
    async def scenario():
        sub = subscription()
        sent = []
        closed = asyncio.Event()

        async def source():
            try:
                # 模拟生成帧与实际发送之间有效期已过；send 本身没有 await 等待点。
                sub.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                yield encode_event(event(1))
            finally:
                closed.set()

        async def send(message):
            sent.append(message)

        async def receive():
            await asyncio.Event().wait()

        response = EventStreamResponse(sub, reading())
        response.body_iterator = source()
        if before_headers:
            sub.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await response({"type": "http"}, receive, send)
        assert not any(b"event:" in message.get("body", b"") for message in sent)
        if before_headers:
            assert sent[0]["status"] == 401
        else:
            assert closed.is_set() and sent[0]["status"] == 200
            assert sent[-1]["more_body"] is False

    asyncio.run(scenario())


def test_header_send_timeout_never_attempts_second_response_start():
    async def scenario():
        sub = subscription(send_timeout=0.02, max_seconds=1)
        sent = []

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.start":
                # 下游可能已经收到响应头，但包装层仍未返回，不能再尝试改成 JSON 响应。
                await asyncio.Event().wait()

        async def receive():
            await asyncio.Event().wait()

        async with asyncio.timeout(1):
            await EventStreamResponse(sub, reading())({"type": "http"}, receive, send)
        assert [message["type"] for message in sent] == ["http.response.start"]

    asyncio.run(scenario())


def test_idle_subscription_ends_at_verified_wall_expiry_and_cleans_limit_watcher():
    async def scenario():
        expires = datetime.now(UTC) + timedelta(seconds=0.07)
        sub = subscription(expires=expires, max_seconds=1)
        closed = asyncio.Event()

        async def source():
            try:
                await asyncio.Event().wait()
                yield HEARTBEAT
            finally:
                closed.set()

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            pass

        response = EventStreamResponse(sub, reading())
        response.body_iterator = source()
        before = asyncio.all_tasks()
        async with asyncio.timeout(0.5):
            await response({"type": "http"}, receive, send)
        assert datetime.now(UTC) >= expires
        assert closed.is_set() and not (asyncio.all_tasks() - before)

    asyncio.run(scenario())
