"""受控 API 独立进程的硬退出与迟到提交；每个进程有随机端口和握手身份。"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from execution_helpers import repository
from test_leases_pg import at, row
from test_runs_pg import account, submit
from test_runs_pg import client as client

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PostgreSQL required"
)


@contextmanager
def api_process(tmp_path, mode):
    directory = tmp_path / uuid4().hex
    directory.mkdir()
    nonce = uuid4().hex
    env = dict(
        os.environ,
        MINIMEW_LEASE_PROCESS="1",
        MINIMEW_KNOWLEDGE_ENABLED="false",
        MINIMEW_DEEPSEEK_API_KEY="test-only-never-network",
        MINIMEW_EXECUTION_LEASE_SECONDS="0.7",
        MINIMEW_EXECUTION_HEARTBEAT_SECONDS="0.1",
        MINIMEW_CANCELLATION_POLL_SECONDS="0.02",
        MINIMEW_EXECUTION_SCAN_SECONDS="0.02",
        MINIMEW_EXECUTION_MAX_ACTIVE="1",
    )
    with open(directory / "process.log", "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("lease_process.py")),
                str(directory),
                mode,
                nonce,
            ],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        metadata = None
        killed = False
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(trust_env=False, timeout=0.4) as browser:
                while True:
                    assert process.poll() is None, "Owned API exited before readiness"
                    assert time.monotonic() < deadline, "Owned API startup timeout"
                    ready = directory / "ready.json"
                    if ready.exists():
                        metadata = json.loads(ready.read_text())
                        assert metadata["nonce"] == nonce
                        base = f"http://127.0.0.1:{metadata['port']}"
                        try:
                            if browser.get(base + "/health/live").status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                    time.sleep(0.01)

            def kill():
                nonlocal killed
                # Windows venv launcher PID 可能不同，使用此子进程握手报告的真实 server PID。
                assert metadata["nonce"] == nonce
                os.kill(metadata["pid"], signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
                killed = True
                process.wait(timeout=10)

            yield base, directory, kill
        finally:
            (directory / "release").touch()
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if metadata is not None:
                        os.kill(
                            metadata["pid"], signal.SIGTERM if os.name == "nt" else signal.SIGKILL
                        )
                    process.kill()
                    process.wait(timeout=5)
                    pytest.fail("Owned API failed bounded shutdown")
            process.stdin.close()
            if not killed:
                assert process.returncode == 0


def wait(client, path, headers, wanted):
    deadline = time.monotonic() + 10
    while True:
        state = client.get(path, headers=headers).json()
        if state["status"] == wanted:
            return state
        assert time.monotonic() < deadline, state
        time.sleep(0.01)


@pytest.mark.parametrize("mode", ["block", "finish_wait"])
def test_crash_or_late_owner_is_fenced_and_next_run_continues(client, tmp_path, mode):
    with api_process(tmp_path, mode) as (base, directory, kill):
        with httpx.Client(base_url=base, trust_env=False, timeout=5) as first:
            headers, tid = account(first)
            created = submit(first, headers, tid, key="original", content="original").json()
            rid = created["id"]
            path = f"/v1/threads/{tid}/runs/{rid}"
            deadline = time.monotonic() + 5
            while True:
                events = first.get(path + "/events", headers=headers).json()["items"]
                if any(e["type"] == "message.delta" for e in events):
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
            if mode == "block":

                async def inspect_renewal():
                    async with repository() as repo:
                        before = await row(repo, UUID(rid))
                        await at(repo, before.lease_until + timedelta(seconds=0.1))
                        after = await row(repo, UUID(rid))
                        assert after.execution_deadline_at == before.execution_deadline_at
                        assert after.lease_until > before.lease_until and after.status == "running"

                asyncio.run(inspect_renewal())
            else:
                while not (directory / "finish-ready.json").exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
            queued = submit(first, headers, tid, key="next", content="next").json()["id"]
            if mode == "block":
                kill()
            with api_process(tmp_path, "answer") as (other, other_dir, _):
                with httpx.Client(base_url=other, trust_env=False, timeout=5) as second:
                    assert (
                        wait(second, path, headers, "failed")["error_code"] == "EXECUTION_EXPIRED"
                    )
                    next_path = f"/v1/threads/{tid}/runs/{queued}"
                    wait(second, next_path, headers, "succeeded")
                    if mode == "finish_wait":
                        (directory / "release").touch()
                        assert first.get(path, headers=headers).json()["status"] == "failed"
                    events = second.get(path + "/events", headers=headers).json()["items"]
                    assert [e["type"] for e in events] == [
                        "run.created",
                        "run.started",
                        "message.delta",
                        "message.completed",
                        "run.failed",
                    ]
                    assert events[-2]["data"]["content"] == "prefix"
                    assert events[-2]["data"]["message_id"] == created["assistant_message_id"]
                    assert json.loads((directory / "calls.json").read_text()) == [rid]
                    assert json.loads((other_dir / "calls.json").read_text()) == [queued]
