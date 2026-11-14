"""Journal作为唯一工具观测事实源，真实PG幂等/资格/回滚。"""

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider, activate_claim, repository
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_agent import completed, executor
from test_execution_pg import create_run, facts
from test_runs_pg import client as client
from test_tools import Result, call

from minimew.execution.journal import append_event
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import CancellationRequested
from minimew.models import EventOutbox, Run, RunEventRecord

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)
data = {"tool_call_id": "call_1", "tool_name": "search"}


def test_committed_ack_loss_reuses_event_without_rerunning_handler(client, monkeypatch):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            original = repo.record_tool
            lost, invoked = False, []

            async def observation(claim, kind, payload):
                nonlocal lost
                result = await original(claim, kind, payload)
                if kind == "tool.completed" and not lost:
                    lost = True
                    raise SQLAlchemyError("injected lost acknowledgement")
                return result

            monkeypatch.setattr(repo, "record_tool", observation)

            async def handler(c, a):
                invoked.append(a.query)
                return Result(text="secret tool output")

            async def answer(request):
                yield (
                    ProviderCompleted(ModelTurn("text", "最终答案"))
                    if invoked
                    else completed(call())
                )

            provider = FakeProvider(answer)
            assert await executor(repo, provider, handler).execute(owner, tid, rid) == "succeeded"
            saved = await facts(repo, rid)
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "tool.started",
                "tool.completed",
                "message.completed",
                "run.completed",
            ]
            assert len(invoked) == 1 and len(provider.requests) == 2
            assert "secret tool output" not in str([e.model_dump() for e in saved["events"]])
            async with repo.sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(EventOutbox)
                    .join(RunEventRecord)
                    .where(RunEventRecord.run_id == rid)
                )
                assert count == len(saved["events"])

    asyncio.run(scenario())


def test_tool_events_idempotent_transition_and_owner_checks(client):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            await activate_claim(repo, claim)
            assert await repo.record_tool(claim, "tool.started", data)
            assert await repo.record_tool(claim, "tool.started", data)
            with pytest.raises(ValueError):
                await repo.record_tool(claim, "tool.started", {**data, "tool_name": "changed"})
            assert await repo.record_tool(claim, "tool.completed", data)
            assert await repo.record_tool(claim, "tool.completed", data)
            with pytest.raises(ValueError):
                await repo.record_tool(
                    claim, "tool.failed", {**data, "error_code": "TOOL_EXECUTION_FAILED"}
                )
            assert not await repo.record_tool(
                replace(claim, owner_token=uuid4()),
                "tool.started",
                {**data, "tool_call_id": "other"},
            )
            saved = await facts(repo, rid)
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "tool.started",
                "tool.completed",
            ]
            await repo.request_cancel(owner, tid, rid)
            with pytest.raises(CancellationRequested):
                await repo.record_tool(
                    claim, "tool.started", {**data, "tool_call_id": "after_cancel"}
                )
            assert await repo.finish(claim, content="ignored") == "cancelled"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("tool.completed", data),
        ("tool.failed", {**data, "error_code": "TOOL_EXECUTION_FAILED"}),
    ],
)
def test_database_rejects_end_without_start_and_rolls_back_cursor(client, kind, payload):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            await activate_claim(repo, claim)
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    row = await session.get(Run, rid)
                    await append_event(session, row, kind, payload)
            saved = await facts(repo, rid)
            assert [e.type for e in saved["events"]] == ["run.created", "run.started"]

    asyncio.run(scenario())
