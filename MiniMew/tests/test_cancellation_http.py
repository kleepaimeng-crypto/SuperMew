"""真实 HTTP、独占 Redis 与两个独立 OS 进程；无模型联网。"""

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from execution_helpers import FakeProvider
from notification_helpers import OwnedRedis
from sse_helpers import frames, next_event, remaining_events, serve_http
from test_cancellation_pg import cancel
from test_runs_pg import account, submit
from test_runs_pg import client as client
from test_runtime_pg import runtime_app, wait_run

from minimew.execution.outbox import notification_lifespan
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.streaming import StreamOptions

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB")
    or os.environ.get("MINIMEW_NOTIFICATION_TESTS") != "1",
    reason="Explicit temporary PostgreSQL / owned Redis / process test required",
)


@pytest.mark.parametrize("redis_down", [False, True])
def test_http_cancel_sse_terminal_replay_and_redis_outage(client, monkeypatch, redis_down):
    with OwnedRedis() as redis:
        monkeypatch.setenv("MINIMEW_REDIS_PORT", str(redis.port))
        monkeypatch.setenv("MINIMEW_REDIS_PASSWORD", redis.password)
        monkeypatch.setenv("MINIMEW_REDIS_KEY_PREFIX", redis.settings().redis_key_prefix)
        monkeypatch.setenv("MINIMEW_NOTIFICATION_SCAN_SECONDS", "0.02")
        monkeypatch.setenv("MINIMEW_CANCELLATION_POLL_SECONDS", "0.02")
        monkeypatch.setattr("minimew.app.notification_lifespan", notification_lifespan)

        async def answer(data):
            yield ProviderDelta("今天")
            await asyncio.Event().wait()
            yield ProviderCompleted("never")

        app = runtime_app(monkeypatch, FakeProvider(answer))
        app.state.event_stream_options = StreamOptions(
            poll_interval=0.1,
            heartbeat_interval=1,
            send_timeout=2,
            max_seconds=15,
        )
        with serve_http(app) as server:
            with httpx.Client(base_url=server.base_url, timeout=8) as browser:
                headers, thread = account(browser)
                result = submit(browser, headers, thread)
                assert result.status_code == 201
                run = result.json()["id"]
            if redis_down:
                asyncio.run(redis.stop())
                assert (
                    redis.docker("inspect", "--format", "{{.State.Running}}", redis.name) == "false"
                )

            async def scenario():
                path = f"/v1/threads/{thread}/runs/{run}"
                async with httpx.AsyncClient(base_url=server.base_url, timeout=8) as browser:
                    async with browser.stream(
                        "GET", path + "/events/stream", headers=headers
                    ) as stream:
                        source = frames(stream)
                        initial = [await next_event(source) for _ in range(3)]
                        assert [e["type"] for e in initial] == [
                            "run.created",
                            "run.started",
                            "message.delta",
                        ]
                        response = await browser.post(path + "/cancel", headers=headers)
                        assert response.status_code == 202
                        assert response.json()["status"] == "running"
                        assert response.json()["cancel_requested_at"]
                        rest = await remaining_events(source)
                    assert [e["type"] for e in rest] == [
                        "run.cancel_requested",
                        "message.completed",
                        "run.cancelled",
                    ]
                    assert rest[-2]["data"] == {
                        "message_id": result.json()["assistant_message_id"],
                        "status": "cancelled",
                        "content": "今天",
                    }
                    async with browser.stream(
                        "GET", path + "/events/stream?after=4", headers=headers
                    ) as stream:
                        replay = await remaining_events(frames(stream))
                    assert [e["sequence"] for e in replay] == [5, 6]
                    exhausted = await browser.get(path + "/events/stream?after=6", headers=headers)
                    assert exhausted.status_code == 204
                    assert (await browser.post(path + "/cancel", headers=headers)).json()[
                        "status"
                    ] == "cancelled"
                    assert (await browser.get(path, headers=headers)).json()[
                        "status"
                    ] == "cancelled"

            asyncio.run(scenario())


@contextmanager
def owned_process(tmp_path, *, owner):
    metadata = tmp_path / ("owner.json" if owner else "receiver.json")
    log_path = metadata.with_suffix(".log")
    env = dict(
        os.environ,
        MINIMEW_DB_NAME=os.environ["MINIMEW_INTEGRATION_DB"],
        MINIMEW_CANCELLATION_TESTS="1",
        MINIMEW_CANCELLATION_POLL_SECONDS="0.02",
        MINIMEW_EXECUTION_SCAN_SECONDS="0.02",
        MINIMEW_DEEPSEEK_API_KEY="test-process-never-network" if owner else "",
    )
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("cancellation_process.py")),
                str(metadata),
            ],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            env=env,
            text=True,
            creationflags=flags,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(timeout=0.3) as browser:
                while True:
                    assert process.poll() is None, "Owned cancellation process failed to start"
                    assert time.monotonic() < deadline, "Owned cancellation process startup timeout"
                    if metadata.exists():
                        base = "http://127.0.0.1:" + str(json.loads(metadata.read_text())["port"])
                        try:
                            if browser.get(base + "/health/live").status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                    time.sleep(0.02)
            yield base, process.pid
        finally:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    pytest.fail("Owned cancellation process failed bounded graceful shutdown")
            process.stdin.close()
            assert process.returncode == 0, "Owned cancellation process failed"


def test_other_process_accepts_cancel_without_model_credentials(client, tmp_path):
    with owned_process(tmp_path, owner=True) as (base, owner_pid):
        with httpx.Client(base_url=base, timeout=8) as browser:
            headers, thread = account(browser)
            result = submit(browser, headers, thread)
            assert result.status_code == 201
            run = result.json()["id"]
            path = f"/v1/threads/{thread}/runs/{run}"
            deadline = time.monotonic() + 6
            while True:
                events = browser.get(path + "/events", headers=headers).json()["items"]
                if any(e["type"] == "message.delta" for e in events):
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
            with owned_process(tmp_path, owner=False) as (other, receiver_pid):
                assert owner_pid != receiver_pid
                with httpx.Client(base_url=other, timeout=8) as receiver:
                    accepted = cancel(receiver, headers, thread, run)
                    assert accepted.status_code == 202
                    deadline = time.monotonic() + 6
                    while True:
                        state = receiver.get(path, headers=headers).json()["status"]
                        if state == "cancelled":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.02)
                    events = receiver.get(path + "/events", headers=headers).json()["items"]
                    assert events[-2]["data"]["content"] == "process-prefix"
                    assert [e["type"] for e in events].count("run.started") == 1
                    assert [e["type"] for e in events].count("run.cancelled") == 1
                    assert str(UUID(run)) == run


def test_cancel_one_run_does_not_stop_other_active_run(client, monkeypatch):
    import threading

    keep_started = threading.Event()
    release_keep = asyncio.Event()

    async def answer(data):
        if data.messages[-1].content == "stop":
            yield ProviderDelta("prefix")
            await asyncio.Event().wait()
        else:
            keep_started.set()
            await release_keep.wait()
            yield ProviderCompleted("other answer")

    app = runtime_app(monkeypatch, FakeProvider(answer))
    with serve_http(app) as server:
        with httpx.Client(base_url=server.base_url, timeout=8) as browser:
            headers, stop_thread = account(browser)
            keep_thread = browser.post("/v1/threads", json={}, headers=headers).json()["id"]
            stop = submit(browser, headers, stop_thread, content="stop").json()["id"]
            keep = submit(browser, headers, keep_thread, content="keep").json()["id"]
            try:
                assert keep_started.wait(5)
                response = cancel(browser, headers, stop_thread, stop)
                assert response.status_code == 202
                deadline = time.monotonic() + 6
                while (
                    browser.get(f"/v1/threads/{stop_thread}/runs/{stop}", headers=headers).json()[
                        "status"
                    ]
                    != "cancelled"
                ):
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                other = browser.get(
                    f"/v1/threads/{keep_thread}/runs/{keep}", headers=headers
                ).json()
                assert other["status"] == "running" and other["cancel_requested_at"] is None
            finally:
                server.loop.call_soon_threadsafe(release_keep.set)
            assert wait_run(browser, headers, keep_thread, keep)["status"] == "succeeded"
