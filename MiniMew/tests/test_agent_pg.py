"""工具循环走原领取/终结事务；仅显式临时PostgreSQL，工具为测试注入。"""

import asyncio
import os
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy.exc import SQLAlchemyError
from test_agent import CAPABILITIES, completed
from test_cancellation_pg import cancel
from test_execution_pg import create_run, facts
from test_runs_pg import client as client
from test_runs_pg import configure_model
from test_tools import Result, call, spec

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderFailure
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def agent(repo, provider, handler, authorize=lambda c, a: True):
    return RunExecutor(
        repo,
        provider,
        registry=ToolRegistry((spec(handler, authorize=authorize),)),
        capabilities=CAPABILITIES,
        cancellation_poll=0.01,
    )


def test_agent_preserves_single_run_message_pair_and_terminal_journal(client):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            before = await facts(repo, run_id)
            calls = []

            async def handler(context, arguments):
                assert (context.user_id, context.thread_id, context.run_id) == (
                    owner,
                    thread,
                    run_id,
                )
                state = await facts(repo, run_id)
                assert state["status"] == "running" and state["messages"][1][2] == ""
                calls.append(arguments.query)
                return Result(text="受控证据")

            async def answer(data):
                if not calls:
                    yield completed(call())
                else:
                    yield ProviderCompleted(ModelTurn("text", "最终答案"))

            provider = FakeProvider(answer)
            executor = agent(repo, provider, handler)
            assert await executor.execute(owner, thread, run_id) == "succeeded"
            assert await executor.execute(owner, thread, run_id) == "not_claimed"
            saved = await facts(repo, run_id)
            assert [m[0] for m in before["messages"]] == [m[0] for m in saved["messages"]]
            assert saved["messages"][1][1:] == ("completed", "最终答案")
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.completed",
            ]
            assert len(provider.requests) == 2 and calls == ["餐费"]

    asyncio.run(scenario())
    assert (
        client.get(f"/v1/threads/{thread}/runs/{run_id}", headers=headers).json()["status"]
        == "succeeded"
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("arguments", "TOOL_INVALID_ARGUMENTS"),
        ("unknown", "TOOL_NOT_AVAILABLE"),
        ("denied", "TOOL_PERMISSION_DENIED"),
        ("tool", "TOOL_EXECUTION_FAILED"),
        ("result", "TOOL_INVALID_RESULT"),
        ("budget", "AGENT_BUDGET_EXCEEDED"),
        ("deadline", "AGENT_DEADLINE_EXCEEDED"),
    ],
)
def test_agent_errors_commit_single_failed_terminal_without_tool_body(
    client, model_configuration, mode, code
):
    if mode == "deadline":
        asyncio.run(
            configure_model(
                "agent-deadline-" + uuid4().hex,
                model_configuration.model_copy(update={"timeout_seconds": 0.1}),
            )
        )
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            invoked = []

            async def handler(c, a):
                invoked.append(a)
                if mode == "tool":
                    raise RuntimeError("private handler credential")
                if mode == "deadline":
                    await asyncio.sleep(1)
                return {"text": 123} if mode == "result" else Result(text="不应公开的工具结果")

            async def answer(data):
                yield completed(
                    call(
                        f"call_{len(data.messages)}",
                        '{"query":123}' if mode == "arguments" else '{"query":"餐费"}',
                        name="unknown" if mode == "unknown" else "search",
                    )
                )

            provider = FakeProvider(answer)
            assert (
                await agent(
                    repo, provider, handler, authorize=lambda c, a: mode != "denied"
                ).execute(owner, thread, run_id)
                == "failed"
            )
            saved = await facts(repo, run_id)
            assert saved["status"] == "failed" and saved["error"] == code
            assert saved["messages"][1][1:] == ("failed", "")
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.failed",
            ]
            assert saved["events"][-1].data.error_code == code
            assert saved["events"][-2].data.content == ""
            assert len(invoked) == (
                0 if mode in ("arguments", "denied", "unknown") else 3 if mode == "budget" else 1
            )

    asyncio.run(scenario())
    response = client.get(f"/v1/threads/{thread}/runs/{run_id}", headers=headers)
    assert response.status_code == 200 and response.json()["error_code"] == code


def test_model_retry_after_tool_success_does_not_duplicate_tool_or_journal(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            invoked, requests = [], []

            async def handler(c, a):
                invoked.append(c.run_id)
                return Result(text="data")

            async def answer(data):
                requests.append(data)
                if len(requests) == 1:
                    yield completed(call())
                elif len(requests) == 2:
                    raise ProviderFailure(retryable=True)
                else:
                    assert data is requests[1]
                    yield ProviderCompleted(ModelTurn("text", "answer"))

            assert (
                await agent(repo, FakeProvider(answer), handler).execute(owner, thread, run_id)
                == "succeeded"
            )
            assert invoked == [run_id] and len(requests) == 3
            saved = await facts(repo, run_id)
            assert [e.type for e in saved["events"]].count("run.started") == 1
            assert [e.type for e in saved["events"]].count("run.completed") == 1

    asyncio.run(scenario())


def test_cancel_during_tool_wait_persists_cancelled_and_never_starts_next_call(client):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            entered, closed = asyncio.Event(), asyncio.Event()
            invoked = []

            async def handler(c, a):
                invoked.append(a)
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

            async def answer(data):
                yield completed(call("one"), call("two"))

            provider = FakeProvider(answer)
            task = asyncio.create_task(
                agent(repo, provider, handler).execute(owner, thread, run_id)
            )
            await asyncio.wait_for(entered.wait(), 3)
            assert cancel(client, headers, thread, run_id).status_code == 202
            assert await asyncio.wait_for(task, 3) == "cancelled"
            saved = await facts(repo, run_id)
            assert closed.is_set() and len(invoked) == 1 and len(provider.requests) == 1
            assert saved["status"] == "cancelled" and saved["error"] is None
            assert saved["messages"][1][1:] == ("cancelled", "")
            assert [e.type for e in saved["events"]].count("run.cancelled") == 1

    asyncio.run(scenario())


def test_expired_owner_cannot_deliver_late_tool_result(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.15) as repo:
            invoked = []

            async def handler(c, a):
                invoked.append(a)
                await asyncio.sleep(0.2)
                return Result(text="late result")

            async def answer(data):
                yield completed(call("one"), call("two"))

            provider = FakeProvider(answer)
            assert (
                await agent(repo, provider, handler).execute(owner, thread, run_id)
                == "ownership_lost"
            )
            saved = await facts(repo, run_id)
            assert len(invoked) == 1 and len(provider.requests) == 1
            assert saved["status"] == "failed" and saved["error"] == "EXECUTION_EXPIRED"
            assert saved["messages"][1][2] == ""
            assert [e.type for e in saved["events"]].count("run.failed") == 1

    asyncio.run(scenario())


def test_qualification_store_failure_before_tool_saves_interruption(client, monkeypatch):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            model_returned = False
            original = repo.check_control

            async def control(claim):
                if model_returned:
                    raise SQLAlchemyError("private qualification store failure")
                await original(claim)

            monkeypatch.setattr(repo, "check_control", control)

            async def handler(c, a):
                raise AssertionError("Must not execute without qualification")

            async def answer(data):
                nonlocal model_returned
                model_returned = True
                yield completed(call())

            assert (
                await agent(repo, FakeProvider(answer), handler).execute(owner, thread, run_id)
                == "failed"
            )
            saved = await facts(repo, run_id)
            assert saved["error"] == "EXECUTION_STORAGE_UNAVAILABLE"
            assert saved["messages"][1][1:] == ("failed", "")
            assert saved["events"][-1].data.error_code == "EXECUTION_STORAGE_UNAVAILABLE"

    asyncio.run(scenario())
