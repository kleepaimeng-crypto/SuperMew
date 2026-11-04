"""显式隔离 PostgreSQL + 临时 Uvicorn socket 的真实增量 HTTP SSE 验收。"""

import asyncio
import os
import time
from threading import Event
from uuid import uuid4

import httpx
import jwt
import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy.exc import SQLAlchemyError
from sse_helpers import frames, next_event, next_heartbeat, remaining_events, serve_http
from test_execution_pg import create_run, facts
from test_runs_pg import account
from test_runs_pg import client as client

from minimew.app import create_app
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.service import RunExecutor
from minimew.security import AUDIENCE, ISSUER

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


@pytest.fixture
def http_server(client):
    from minimew.execution.streaming import StreamOptions

    # client 先校验随机临时库名、配置显式 fake 模型；另建 app，避免跨 loop 复用 Engine。
    app = create_app()
    app.state.event_stream_options = StreamOptions(
        poll_interval=0.025,
        heartbeat_interval=0.075,
        send_timeout=1,
        max_seconds=5,
        page_size=2,
    )
    with serve_http(app) as server:
        yield server


def url(thread, run):
    return f"/v1/threads/{thread}/runs/{run}/events/stream"


def assert_stream(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"


def canonical(saved):
    return [event.model_dump(mode="json") for event in saved["events"]]


def test_http_incremental_before_terminal_disconnect_and_explicit_cursor_replay(
    client, http_server
):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=6) as browser,
        ):
            published, release = asyncio.Event(), asyncio.Event()

            async def answer(request):
                yield ProviderDelta("今天")
                published.set()  # 执行器已提交该 delta，才会索取 Provider 下一项。
                await release.wait()
                yield ProviderDelta("晴朗")
                yield ProviderCompleted("今天晴朗。")

            provider = FakeProvider(answer)
            task = None
            try:
                async with browser.stream("GET", url(thread, run_id), headers=headers) as response:
                    assert_stream(response)
                    source = frames(response)
                    first = await next_event(source)
                    assert first["type"] == "run.created"
                    assert (await facts(repo, run_id))["status"] == "pending"
                    assert provider.requests == []  # 订阅不能启动执行。
                    task = asyncio.create_task(
                        RunExecutor(repo, provider).execute(owner, thread, run_id)
                    )
                    await asyncio.wait_for(published.wait(), 5)
                    started, delta = await next_event(source), await next_event(source)
                    assert started["type"] == "run.started"
                    assert delta["type"] == "message.delta" and delta["data"]["content"] == "今天"
                    assert delta["sequence"] == 3 and not task.done()
                    running = await facts(repo, run_id)
                    assert running["status"] == "running"
                    assert running["messages"][1][1:] == ("pending", "")
                await http_server.idle(disconnected=True)
                assert not task.done()  # 真实 HTTP 断线只释放订阅。
            finally:
                release.set()
                if task is not None:
                    assert await asyncio.wait_for(task, 5) == "succeeded"
            saved = await facts(repo, run_id)
            replay_headers = dict(headers, **{"Last-Event-ID": "999999"})
            async with browser.stream(
                "GET",
                url(thread, run_id),
                params={"after": delta["sequence"]},
                headers=replay_headers,
            ) as response:
                assert_stream(response)
                replay = await remaining_events(frames(response))
            assert replay == canonical(saved)[3:]
            assert [event["type"] for event in replay] == [
                "message.delta",
                "message.completed",
                "run.completed",
            ]
            assert len(provider.requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failed", [False, True], ids=["succeeded", "failed"])
def test_http_terminal_backlog_drains_all_pages_then_returns_204(client, http_server, failed):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=6) as browser,
        ):
            claim = await repo.claim(owner, thread, run_id)
            for content in ["一", "二", "三", "四", "五"]:
                assert await repo.append_delta(claim, content)
            if failed:
                assert await repo.finish(claim, error_code="PROVIDER_ERROR")
            else:
                assert await repo.finish(claim, content="一二三四五。")
            saved = await facts(repo, run_id)
            async with browser.stream("GET", url(thread, run_id), headers=headers) as response:
                assert_stream(response)
                received = await remaining_events(frames(response))
            assert received == canonical(saved)
            assert len(received) == 9
            assert received[-1]["type"] == ("run.failed" if failed else "run.completed")
            drained = await browser.get(url(thread, run_id), params={"after": 9}, headers=headers)
            assert drained.status_code == 204 and drained.content == b""
            assert drained.headers["cache-control"] == "no-store"
            assert await facts(repo, run_id) == saved
            await http_server.idle(disconnected=True)

    asyncio.run(scenario())


def test_http_sse_auth_ownership_mismatch_and_cursor_bounds(client, http_server):
    headers, _, thread, run_id = create_run(client)
    foreign_headers, foreign_thread = account(client)

    async def scenario():
        async with httpx.AsyncClient(base_url=http_server.base_url, timeout=5) as browser:
            path = url(thread, run_id)
            for credentials in [{}, {"Authorization": "Bearer malformed"}]:
                denied = await browser.get(path, headers=credentials)
                assert denied.status_code == 401
                assert denied.headers["www-authenticate"] == "Bearer"
            for path, credentials in [
                (path, foreign_headers),
                (url(foreign_thread, run_id), headers),
                (url(thread, uuid4()), headers),
            ]:
                denied = await browser.get(path, headers=credentials)
                assert denied.status_code == 404
            for cursor in [-1, "invalid", 2**63, 2]:
                denied = await browser.get(
                    url(thread, run_id), params={"after": cursor}, headers=headers
                )
                assert denied.status_code == 422
                assert "text/event-stream" not in denied.headers.get("content-type", "")
            await http_server.idle(disconnected=True)

    asyncio.run(scenario())


def test_http_idle_heartbeats_and_disconnect_release_pool_without_run_writes(client, http_server):
    headers, _, thread, run_id = create_run(client)

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=6) as browser,
        ):
            before = await facts(repo, run_id)
            async with browser.stream(
                "GET", url(thread, run_id), params={"after": 1}, headers=headers
            ) as response:
                assert_stream(response)
                source = frames(response)
                await next_heartbeat(source)
                await http_server.idle()
                await next_heartbeat(source)
                assert await facts(repo, run_id) == before
            await http_server.idle(disconnected=True)
            assert await facts(repo, run_id) == before

    asyncio.run(scenario())


def test_http_two_overlapping_subscriptions_are_isolated_and_disconnect_is_local(
    client, http_server
):
    first_headers, first_owner, first_thread, first_run = create_run(client, content="one")
    second_headers, second_owner, second_thread, second_run = create_run(client, content="two")

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=6) as browser,
        ):
            async with browser.stream(
                "GET", url(second_thread, second_run), headers=second_headers
            ) as second:
                assert_stream(second)
                second_source = frames(second)
                second_events = [await next_event(second_source)]
                async with browser.stream(
                    "GET", url(first_thread, first_run), headers=first_headers
                ) as first:
                    assert_stream(first)
                    first_source = frames(first)
                    first_events = [await next_event(first_source)]
                    first_claim, second_claim = await asyncio.gather(
                        repo.claim(first_owner, first_thread, first_run),
                        repo.claim(second_owner, second_thread, second_run),
                    )
                    first_events.append(await next_event(first_source))
                    second_events.append(await next_event(second_source))
                    assert await repo.append_delta(first_claim, "first-only")
                    first_events.append(await next_event(first_source))
                    # 第二个流在第一个 Run 新增 delta 后仍然只有自己的 heartbeat。
                    await next_heartbeat(second_source)
                    assert await repo.append_delta(second_claim, "second-only")
                    second_events.append(await next_event(second_source))
                assert (await facts(repo, first_run))["status"] == "running"
                assert (await facts(repo, second_run))["status"] == "running"
                assert await repo.finish(first_claim, content="first final")
                assert await repo.finish(second_claim, content="second final")
                second_events.extend(await remaining_events(second_source))
            for events, thread, run_id in [
                (first_events, first_thread, first_run),
                (second_events, second_thread, second_run),
            ]:
                assert all(e["run_id"] == str(run_id) for e in events)
                assert all(e["thread_id"] == str(thread) for e in events)
                assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
            assert first_events[-1]["data"]["content"] == "first-only"
            assert second_events == canonical(await facts(repo, second_run))
            await http_server.idle(disconnected=True)

    asyncio.run(scenario())


def test_http_verified_access_expiry_closes_subscription_and_keeps_run_running(client, http_server):
    _, owner, thread, run_id = create_run(client)
    # 仅使用 conftest 注入的独立测试 Secret；不读取 .env 或解析未验签生产令牌。
    assert os.environ["MINIMEW_JWT_SECRET"].startswith("test-only-")
    now = int(time.time())
    expires_at = now + 2
    token = jwt.encode(
        {
            "sub": str(owner),
            "iat": now,
            "exp": expires_at,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "token_type": "access",
            "jti": str(uuid4()),
        },
        http_server.app.state.settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    headers = {"Authorization": "Bearer " + token}

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=6) as browser,
        ):
            claim = await repo.claim(owner, thread, run_id)
            before = await facts(repo, run_id)
            assert before["status"] == "running"
            async with browser.stream(
                "GET", url(thread, run_id), params={"after": 2}, headers=headers
            ) as response:
                assert_stream(response)
                source = frames(response)
                await next_heartbeat(source)
                # 必须因已验签 exp 及时结束，不能等到 max_seconds=5 才伪装为到期清理。
                async with asyncio.timeout(max(0.1, expires_at - time.time() + 1)):
                    assert await remaining_events(source) == []
            assert time.time() >= expires_at
            await http_server.idle(disconnected=True)
            assert await facts(repo, run_id) == before
            expired = await browser.get(url(thread, run_id), headers=headers)
            assert expired.status_code == 401
            assert await repo.finish(claim, content="execution continues after subscriber expiry")

    asyncio.run(scenario())


@pytest.mark.parametrize("error_type", [SQLAlchemyError, TimeoutError])
def test_http_preflight_store_failure_is_503_before_sse_headers(
    client, http_server, monkeypatch, error_type
):
    import minimew.execution.streaming as streaming

    headers, _, thread, run_id = create_run(client)

    async def unavailable(*args, **kwargs):
        raise error_type("injected-private-store-detail")

    monkeypatch.setattr(streaming, "events_after", unavailable)

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=5) as browser,
        ):
            before = await facts(repo, run_id)
            response = await browser.get(url(thread, run_id), headers=headers)
            assert response.status_code == 503
            assert "text/event-stream" not in response.headers.get("content-type", "")
            assert "injected-private-store-detail" not in response.text
            assert await facts(repo, run_id) == before
            await http_server.idle(disconnected=True)

    asyncio.run(scenario())


def test_http_read_failure_after_headers_closes_only_subscriber(client, http_server, monkeypatch):
    import minimew.execution.streaming as streaming

    headers, owner, thread, run_id = create_run(client)
    original = streaming.events_after
    inject_fault, fault_seen = Event(), Event()

    async def read_or_fail(*args, **kwargs):
        if inject_fault.is_set():
            fault_seen.set()
            raise SQLAlchemyError("injected-private-store-detail")
        return await original(*args, **kwargs)

    monkeypatch.setattr(streaming, "events_after", read_or_fail)

    async def scenario():
        async with (
            repository() as repo,
            httpx.AsyncClient(base_url=http_server.base_url, timeout=5) as browser,
        ):
            claim = await repo.claim(owner, thread, run_id)
            before = await facts(repo, run_id)
            async with browser.stream(
                "GET", url(thread, run_id), params={"after": 2}, headers=headers
            ) as response:
                assert_stream(response)
                source = frames(response)
                await next_heartbeat(source)
                inject_fault.set()
                assert await asyncio.to_thread(fault_seen.wait, 2)
                async with asyncio.timeout(2):
                    assert await remaining_events(source) == []
            await http_server.idle(disconnected=True)
            assert await facts(repo, run_id) == before
            assert before["status"] == "running"
            assert await repo.finish(claim, content="independent executor still owns Run")

    asyncio.run(scenario())
