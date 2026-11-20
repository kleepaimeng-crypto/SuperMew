"""真实 PostgreSQL / Redis / HTTP 下使用同一正式执行器自动驱动。"""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from execution_helpers import FakeProvider, activate_claim, lease_remaining, repository
from notification_helpers import OwnedRedis
from pydantic import SecretStr
from sqlalchemy import select
from sse_helpers import frames, remaining_events, serve_http
from test_execution_pg import create_run, facts
from test_runs_pg import account, submit
from test_runs_pg import client as client

from minimew.app import create_app
from minimew.execution.dispatcher import RunDispatcher, execution_lifespan
from minimew.execution.messages import ModelTurn
from minimew.execution.outbox import notification_lifespan
from minimew.execution.provider import ProviderCompleted, ProviderDelta, ProviderFailure
from minimew.models import Run

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB")
    or os.environ.get("MINIMEW_NOTIFICATION_TESTS") != "1",
    reason="Explicit temporary PostgreSQL and owned Redis required",
)


def wait_run(browser, headers, thread, run):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = browser.get(f"/v1/threads/{thread}/runs/{run}", headers=headers)
        assert response.status_code == 200, response.text
        if response.json()["status"] in ("succeeded", "failed"):
            return response.json()
        time.sleep(0.01)
    raise AssertionError("Automatic Run did not terminate")


def runtime_app(monkeypatch, provider):
    monkeypatch.setattr("minimew.app.execution_lifespan", execution_lifespan)
    monkeypatch.setattr(
        "minimew.execution.chat_provider.ChatProvider", lambda key, **kwargs: provider
    )
    monkeypatch.setenv("MINIMEW_EXECUTION_SCAN_SECONDS", "0.01")
    return create_app()


def test_missing_key_rejects_only_new_work_after_idempotency(client):
    headers, _, thread, run = create_run(client)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"deepseek_api_key": SecretStr("")}
    )
    assert submit(client, headers, str(thread), key="first", content="question").json()[
        "id"
    ] == str(run)
    assert submit(client, headers, str(thread), key="first", content="changed").status_code == 409
    response = submit(client, headers, str(thread), key="new", content="question")
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "MODEL_CONFIGURATION_UNAVAILABLE"}


def test_post_automatically_executes_and_sse_replays_with_real_redis(client, monkeypatch):
    with OwnedRedis() as redis:
        monkeypatch.setenv("MINIMEW_REDIS_PORT", str(redis.port))
        monkeypatch.setenv("MINIMEW_REDIS_PASSWORD", redis.password)
        monkeypatch.setenv("MINIMEW_REDIS_KEY_PREFIX", redis.settings().redis_key_prefix)
        monkeypatch.setenv("MINIMEW_NOTIFICATION_SCAN_SECONDS", "0.02")
        monkeypatch.setattr("minimew.app.notification_lifespan", notification_lifespan)

        async def answer(data):
            yield ProviderDelta("今天")
            await asyncio.sleep(0.1)
            yield ProviderDelta("很好")
            yield ProviderCompleted(ModelTurn("text", "今天很好"))

        provider = FakeProvider(answer)
        with serve_http(runtime_app(monkeypatch, provider)) as server:
            with httpx.Client(base_url=server.base_url, timeout=8) as browser:
                headers, thread = account(browser)
                result = submit(browser, headers, thread)
                assert result.status_code == 201
                run = result.json()["id"]

            async def read():
                async with httpx.AsyncClient(base_url=server.base_url, timeout=8) as browser:
                    async with browser.stream(
                        "GET", f"/v1/threads/{thread}/runs/{run}/events/stream", headers=headers
                    ) as response:
                        assert response.status_code == 200
                        events = await remaining_events(frames(response))
                    assert [e["sequence"] for e in events] == list(range(1, 7))
                    assert [e["type"] for e in events] == [
                        "run.created",
                        "run.started",
                        "message.delta",
                        "message.delta",
                        "message.completed",
                        "run.completed",
                    ]
                    assert events[-2]["data"]["content"] == "今天很好"
                    async with browser.stream(
                        "GET",
                        f"/v1/threads/{thread}/runs/{run}/events/stream?after=4",
                        headers=headers,
                    ) as response:
                        replay = await remaining_events(frames(response))
                    assert [e["sequence"] for e in replay] == [5, 6]

            asyncio.run(read())
            own = [r for r in provider.requests if str(r.run_id) == run]
            assert len(own) == 1


def test_same_thread_fifo_and_later_run_receives_previous_answer(client, monkeypatch):
    async def answer(data):
        await asyncio.sleep(0.08)
        yield ProviderCompleted(ModelTurn("text", "answer:" + data.messages[-1].content))

    provider = FakeProvider(answer)
    with serve_http(runtime_app(monkeypatch, provider)) as server:
        with httpx.Client(base_url=server.base_url, timeout=8) as browser:
            headers, thread = account(browser)
            runs = [
                submit(browser, headers, thread, key=text, content=text).json()["id"]
                for text in ("A", "B", "C")
            ]
            for run in runs:
                assert wait_run(browser, headers, thread, run)["status"] == "succeeded"
        own = [r for r in provider.requests if str(r.thread_id) == thread]
        assert [r.messages[-1].content for r in own] == ["A", "B", "C"]
        assert [m.content for m in own[-1].messages] == ["A", "answer:A", "B", "answer:B", "C"]


def test_cross_thread_parallelism_is_bounded_per_instance(client, monkeypatch):
    active, peak = 0, 0

    async def answer(data):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.25)
            yield ProviderCompleted(ModelTurn("text", "answer"))
        finally:
            active -= 1

    provider = FakeProvider(answer)
    with serve_http(runtime_app(monkeypatch, provider)) as server:
        with httpx.Client(base_url=server.base_url, timeout=8) as browser:
            headers, first = account(browser)
            threads = [first] + [
                browser.post("/v1/threads", json={}, headers=headers).json()["id"] for _ in range(3)
            ]
            runs = [(thread, submit(browser, headers, thread).json()["id"]) for thread in threads]
            for thread, run in runs:
                assert wait_run(browser, headers, thread, run)["status"] == "succeeded"
        assert peak == 2 and active == 0


@asynccontextmanager
async def drive(dispatcher):
    task = asyncio.create_task(dispatcher.run())
    try:
        yield
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await dispatcher.drain()


async def completed(repo, run):
    async with asyncio.timeout(6):
        while True:
            # 后台仍在提交时只读取一个状态快照；终态后才复核完整 Journal。
            async with repo.sessions() as session:
                state = await session.scalar(select(Run.status).where(Run.id == run))
            if state in ("failed", "succeeded"):
                break
            await asyncio.sleep(0.01)
    return await facts(repo, run)


def test_two_dispatchers_compete_but_call_provider_once(client):
    _, _, _, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            provider = FakeProvider()
            first = RunDispatcher(repo, provider, scan_seconds=0.01)
            second = RunDispatcher(repo, provider, scan_seconds=0.01)
            async with drive(first), drive(second):
                assert (await completed(repo, run))["status"] == "succeeded"
            assert sum(r.run_id == run for r in provider.requests) == 1
            assert not first.active and not second.active

    asyncio.run(scenario())


def test_restart_keeps_valid_running_and_resumes_pending(client):
    _, owner, thread, running = create_run(client)
    _, _, _, pending = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, running)
            await activate_claim(repo, claim)
            provider = FakeProvider()
            # 模拟重启后新的本进程任务集合为空；仍由数据库资格决定。
            async with drive(RunDispatcher(repo, provider, scan_seconds=0.01)):
                assert (await completed(repo, pending))["status"] == "succeeded"
            assert (await facts(repo, running))["status"] == "running"
            assert all(r.run_id != running for r in provider.requests)
            assert await repo.finish(claim, content="original owner")

    asyncio.run(scenario())


def test_missing_key_still_recovers_expired_prefix_without_model_call(client):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.2) as repo:
            claim = await repo.claim(owner, thread, run)
            await activate_claim(repo, claim)
            assert await repo.append_delta(claim, "今天")
            while await lease_remaining(repo, claim) > 0:
                await asyncio.sleep(0.01)
            async with drive(RunDispatcher(repo, None, scan_seconds=0.01)):
                result = await completed(repo, run)
            assert result["error"] == "EXECUTION_EXPIRED"
            assert result["messages"][1][2] == "今天"
            assert sum(e.type == "run.failed" for e in result["events"]) == 1

    asyncio.run(scenario())


def test_blocked_thread_does_not_starve_other_candidates(client):
    headers, owner, blocked, first = create_run(client)
    for i in range(5):
        assert submit(client, headers, str(blocked), key=f"queued{i}").status_code == 201
    _, _, _, other = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, blocked, first)
            await activate_claim(repo, claim)
            candidates = await repo.candidates(limit=100)
            assert other in [row[2] for row in candidates]
            assert all(row[1] != blocked for row in candidates)
            assert await repo.finish(claim, content="original answer")

    asyncio.run(scenario())


def test_retry_persists_single_pair_and_terminal_event(client):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            count = 0

            async def answer(data):
                nonlocal count
                count += 1
                if count == 1:
                    raise ProviderFailure(retryable=True)
                yield ProviderDelta("answer")
                yield ProviderCompleted(ModelTurn("text", "answer"))

            from minimew.execution.service import RunExecutor

            assert (
                await RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run)
                == "succeeded"
            )
            result = await facts(repo, run)
            assert count == 2 and len(result["messages"]) == 2
            assert sum(e.type == "run.started" for e in result["events"]) == 1
            assert sum(e.type == "run.completed" for e in result["events"]) == 1
            async with repo.sessions() as session:
                assert (
                    await session.scalar(select(Run).where(Run.id == UUID(str(run))))
                ).status == "succeeded"

    asyncio.run(scenario())
