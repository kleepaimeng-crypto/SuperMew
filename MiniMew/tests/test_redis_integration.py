"""显式临时 Redis + PostgreSQL + HTTP：通知不是第二套业务事实。"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from execution_helpers import FakeProvider, repository
from notification_helpers import OwnedRedis
from sqlalchemy.exc import SQLAlchemyError
from sse_helpers import frames, next_event, remaining_events, serve_http
from test_execution_pg import create_run, facts
from test_outbox_pg import clear_previous_outbox, rows_for
from test_runs_pg import account
from test_runs_pg import client as client
from test_sse import event

from minimew.app import create_app
from minimew.execution.notifications import RedisNotifications
from minimew.execution.outbox import OutboxPublisher, notification_lifespan
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.service import RunExecutor
from minimew.execution.streaming import StreamOptions

pytestmark = pytest.mark.skipif(
    os.environ.get("MINIMEW_NOTIFICATION_TESTS") != "1"
    or not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit owned Redis and temporary PostgreSQL required",
)


@pytest.fixture(scope="session")
def redis_server():
    with OwnedRedis() as server:
        yield server


@pytest.fixture
def clean_client(client):
    async def clean():
        async with repository() as repo:
            await clear_previous_outbox(repo)

    asyncio.run(clean())
    return client


def prepare_app(monkeypatch, server, *, poll=2.0, controlled=False):
    import minimew.app as app_module

    settings = server.settings()
    monkeypatch.setenv("MINIMEW_REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("MINIMEW_REDIS_PORT", str(server.port))
    monkeypatch.setenv("MINIMEW_REDIS_PASSWORD", server.password)
    monkeypatch.setenv("MINIMEW_REDIS_KEY_PREFIX", settings.redis_key_prefix)
    monkeypatch.setenv("MINIMEW_NOTIFICATION_SCAN_SECONDS", "0.02")

    @asynccontextmanager
    async def controlled_lifespan(sessions, settings):
        transport = RedisNotifications(settings)
        try:
            yield transport
        finally:
            await transport.aclose()

    monkeypatch.setattr(
        app_module,
        "notification_lifespan",
        controlled_lifespan if controlled else notification_lifespan,
    )
    app = create_app()
    app.state.event_stream_options = StreamOptions(
        poll_interval=poll, heartbeat_interval=3, send_timeout=2, max_seconds=15, page_size=2
    )
    return app


def path(thread, run):
    return f"/v1/threads/{thread}/runs/{run}/events/stream"


def test_real_stream_dedup_high_sequence_ttl_and_two_independent_readers(redis_server):
    async def scenario():
        settings = redis_server.settings().model_copy(
            update={"redis_stream_maxlen": 2, "redis_stream_ttl_seconds": 1}
        )
        transport = RedisNotifications(settings)
        original = event(1)
        try:
            one = asyncio.create_task(transport.wait_after(original.run_id, 0, 2))
            two = asyncio.create_task(transport.wait_after(original.run_id, 0, 2))
            async with asyncio.timeout(1):
                while len(transport.reader.connection_pool._in_use_connections) != 2:
                    await asyncio.sleep(0.005)
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            assert await transport.publish(original)
            assert await two == 1
            assert await transport.wait_after(original.run_id, 0, 0.2) == 1
            assert not await transport.publish(original)
            assert await transport.writer.xlen(transport.stream_key(original.run_id)) == 1
            # bigint 序号不经过 Lua 浮点比较。
            huge = original.model_copy(update={"sequence": 2**53 + 1})
            assert await transport.publish(huge)
            assert not await transport.publish(original)
            for index in range(2**53 + 2, 2**53 + 222):
                await transport.publish(original.model_copy(update={"sequence": index}))
            key = transport.stream_key(original.run_id)
            assert await transport.writer.xlen(key) < 222
            assert await transport.writer.ttl(key) > 0
            async with asyncio.timeout(3):
                while await transport.writer.exists(key):
                    await asyncio.sleep(0.03)
            assert await transport.wait_after(original.run_id, 1, 0.03) == 1
        finally:
            await transport.aclose()

    asyncio.run(scenario())


def test_real_publish_then_mark_failure_reuses_notification_identity(
    clean_client, redis_server, monkeypatch
):
    _, _, _, run = create_run(clean_client)

    async def scenario():
        async with repository() as repo:
            transport = RedisNotifications(redis_server.settings())
            try:
                first = OutboxPublisher(repo.sessions, transport)
                monkeypatch.setattr(
                    first,
                    "record_result",
                    AsyncMock(side_effect=SQLAlchemyError("private database error")),
                )
                with pytest.raises(SQLAlchemyError):
                    await first.publish_once()
                assert (await rows_for(repo, run))[0].published_at is None
                assert await OutboxPublisher(repo.sessions, transport).publish_once() == 1
                assert await transport.writer.xlen(transport.stream_key(run)) == 1
                assert len((await facts(repo, run))["events"]) == 1
            finally:
                await transport.aclose()

    asyncio.run(scenario())


def test_http_two_subscriptions_wake_before_poll_and_disconnect_is_independent(
    clean_client, redis_server, monkeypatch
):
    headers, owner, thread, run = create_run(clean_client)
    app = prepare_app(monkeypatch, redis_server)
    with serve_http(app) as server:

        async def scenario():
            async with (
                repository() as repo,
                httpx.AsyncClient(base_url=server.base_url, timeout=7) as browser,
            ):
                release, partial = asyncio.Event(), asyncio.Event()

                async def answer(request):
                    yield ProviderDelta("今天")
                    partial.set()
                    await release.wait()
                    yield ProviderCompleted("今天晴朗。")

                task = None
                first = browser.stream("GET", path(thread, run), headers=headers)
                try:
                    response = await first.__aenter__()
                    one = frames(response)
                    assert (await next_event(one))["sequence"] == 1
                    async with browser.stream(
                        "GET", path(thread, run), params={"after": 1}, headers=headers
                    ) as other:
                        two = frames(other)
                        task = asyncio.create_task(
                            RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run)
                        )
                        await asyncio.wait_for(partial.wait(), 3)
                        async with asyncio.timeout(1):
                            assert [(await next_event(one))["sequence"] for _ in range(2)] == [2, 3]
                            assert [(await next_event(two))["sequence"] for _ in range(2)] == [2, 3]
                        await first.__aexit__(None, None, None)
                        first = None
                        assert not task.done()
                        await server.idle()
                        release.set()
                        remaining = await remaining_events(two)
                        assert [e["sequence"] for e in remaining] == [4, 5]
                        assert await task == "succeeded"

                        # 发布任务会合法地短暂借连接；在服务器 loop 中有界观察归还，
                        # 不把跨 loop 的瞬时采样误认为泄漏。
                        async def assert_released():
                            async with asyncio.timeout(1):
                                while app.state.session_factory.kw["bind"].pool.checkedout():
                                    await asyncio.sleep(0.005)
                                assert app.state.session_factory.kw["bind"].pool.checkedout() == 0

                        await asyncio.wrap_future(
                            asyncio.run_coroutine_threadsafe(assert_released(), server.loop)
                        )
                finally:
                    release.set()
                    if first is not None:
                        await first.__aexit__(None, None, None)
                    if task is not None:
                        await asyncio.wait_for(task, 3)

        asyncio.run(scenario())


def test_http_redis_lag_and_untrusted_copy_still_replay_whole_journal(
    clean_client, redis_server, monkeypatch
):
    headers, owner, thread, run = create_run(clean_client)
    app = prepare_app(monkeypatch, redis_server, controlled=True)
    with serve_http(app) as server:

        async def scenario():
            async with (
                repository() as repo,
                httpx.AsyncClient(base_url=server.base_url, timeout=7) as browser,
            ):
                async with browser.stream(
                    "GET", path(thread, run), params={"after": 1}, headers=headers
                ) as response:

                    async def answer(request):
                        yield ProviderDelta("正文来自 PostgreSQL")
                        yield ProviderCompleted("最终正文来自 PostgreSQL")

                    assert (
                        await RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run)
                        == "succeeded"
                    )
                    saved = await facts(repo, run)
                    delta = saved["events"][2]
                    copy = delta.model_copy(
                        update={
                            "data": delta.data.model_copy(
                                update={"content": "incorrect Redis copy"}
                            )
                        }
                    )
                    transport = RedisNotifications(
                        redis_server.settings().model_copy(
                            update={"redis_key_prefix": app.state.settings.redis_key_prefix}
                        )
                    )
                    try:
                        await transport.publish(copy)
                    finally:
                        await transport.aclose()
                    async with asyncio.timeout(1):
                        received = await remaining_events(frames(response))
                    assert received == [e.model_dump(mode="json") for e in saved["events"][1:]]
                    assert "incorrect Redis copy" not in json.dumps(received)

        asyncio.run(scenario())


def test_http_redis_down_keeps_run_and_pg_replay_then_restarts_pending_outbox(
    clean_client, redis_server, monkeypatch
):
    headers, owner, thread, run = create_run(clean_client)
    app = prepare_app(monkeypatch, redis_server, poll=0.15)
    with serve_http(app) as server:

        async def scenario():
            async with (
                repository() as repo,
                httpx.AsyncClient(base_url=server.base_url, timeout=7) as browser,
            ):
                try:
                    async with browser.stream(
                        "GET", path(thread, run), headers=headers
                    ) as response:
                        source = frames(response)
                        assert (await next_event(source))["sequence"] == 1
                        await redis_server.stop()
                        assert (await browser.get("/health/ready")).status_code == 200
                        assert (
                            await RunExecutor(repo, FakeProvider()).execute(owner, thread, run)
                            == "succeeded"
                        )
                        received = await remaining_events(source)
                        assert [e["sequence"] for e in received] == [2, 3, 4]
                        assert any(row.published_at is None for row in await rows_for(repo, run))
                        assert (await facts(repo, run))["status"] == "succeeded"
                finally:
                    await redis_server.start()
                async with asyncio.timeout(7):
                    while any(row.published_at is None for row in await rows_for(repo, run)):
                        await asyncio.sleep(0.03)
                assert (await facts(repo, run))["status"] == "succeeded"

        asyncio.run(scenario())


def test_api_starts_while_redis_is_down_and_later_publishes_pending(
    clean_client, redis_server, monkeypatch
):
    headers, thread_text = account(clean_client)
    owner = UUID(clean_client.get("/auth/me", headers=headers).json()["id"])
    thread = UUID(thread_text)
    asyncio.run(redis_server.stop())
    try:
        app = prepare_app(monkeypatch, redis_server, poll=0.15)
        with serve_http(app) as server:

            async def scenario():
                async with (
                    repository() as repo,
                    httpx.AsyncClient(base_url=server.base_url, timeout=7) as browser,
                ):
                    assert (await browser.get("/health/ready")).status_code == 200
                    reserved = await browser.post(
                        f"/v1/threads/{thread}/runs",
                        headers=headers,
                        json={"idempotency_key": "down-at-startup", "content": "question"},
                    )
                    assert reserved.status_code == 201
                    run = UUID(reserved.json()["id"])
                    assert (
                        await RunExecutor(repo, FakeProvider()).execute(owner, thread, run)
                        == "succeeded"
                    )
                    async with browser.stream(
                        "GET", path(thread, run), headers=headers
                    ) as response:
                        assert [
                            e["sequence"] for e in await remaining_events(frames(response))
                        ] == [1, 2, 3, 4]
                    assert all(row.published_at is None for row in await rows_for(repo, run))
                    await redis_server.start()
                    async with asyncio.timeout(7):
                        while any(row.published_at is None for row in await rows_for(repo, run)):
                            await asyncio.sleep(0.03)

            asyncio.run(scenario())
    finally:
        asyncio.run(redis_server.start())
