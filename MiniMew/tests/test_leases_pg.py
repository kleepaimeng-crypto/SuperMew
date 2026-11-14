"""短租约与活动预算分离；真实 PG/屏障，受控 Provider，不联网。"""

import asyncio
import os
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from test_execution_pg import create_run, facts
from test_runs_pg import client as client
from test_runs_pg import configure_model

from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted
from minimew.execution.repository import CancellationRequested, Claim, OwnershipLost
from minimew.execution.service import RunExecutor
from minimew.models import Run

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


async def now(repo):
    async with repo.sessions() as session:
        return await session.scalar(select(func.clock_timestamp()))


async def at(repo, instant):
    async with asyncio.timeout(5):
        while await now(repo) < instant:
            await asyncio.sleep(0.005)  # 真实业务时钟；竞争本身由屏障控制。


async def row(repo, rid):
    async with repo.sessions() as session:
        return await session.get(Run, rid)


def test_bootstrap_cannot_publish_and_activation_is_idempotent(client):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            assert await repo.remaining(claim) == 0
            assert await repo.append_delta(claim, "premature") is False
            assert (
                await repo.record_tool(
                    claim, "tool.started", {"tool_call_id": "one", "tool_name": "read"}
                )
                is False
            )
            with pytest.raises(ValueError, match="activated"):
                await repo.heartbeat(claim)
            with pytest.raises(IntegrityError):
                await repo.finish(claim, content="premature success")
            remaining = await repo.activate(claim, 1)
            first = await row(repo, rid)
            second_remaining = await repo.activate(claim, 1)
            second = await row(repo, rid)
            assert 0 < second_remaining < remaining <= 1
            assert first.execution_deadline_at == second.execution_deadline_at
            assert await repo.finish(claim, content="done") == "succeeded"

    asyncio.run(scenario())


def test_healthy_executor_outlives_first_lease_without_new_model_budget(
    client, model_configuration
):
    asyncio.run(
        configure_model(
            "short-" + uuid4().hex, model_configuration.model_copy(update={"timeout_seconds": 1.5})
        )
    )
    _, owner, tid, rid = create_run(client)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def answer(request):
            entered.set()
            await release.wait()
            yield ProviderCompleted(ModelTurn("text", "same-owner"))

        async with repository(lease_seconds=0.3) as repo:
            provider = FakeProvider(answer)
            executor = RunExecutor(repo, provider, cancellation_poll=0.01, heartbeat_seconds=0.05)
            task = asyncio.create_task(executor.execute(owner, tid, rid))
            await asyncio.wait_for(entered.wait(), 3)
            original = await row(repo, rid)
            await at(repo, original.lease_until + timedelta(seconds=0.08))
            renewed = await row(repo, rid)
            assert renewed.lease_until > original.lease_until
            assert renewed.execution_deadline_at == original.execution_deadline_at
            release.set()
            assert await task == "succeeded" and len(provider.requests) == 1
            saved = await facts(repo, rid)
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.completed",
            ]
            assert not any(
                t.get_name().startswith(("minimew-model-", "minimew-cancel-monitor-"))
                for t in asyncio.all_tasks()
                if t is not asyncio.current_task()
            )

    asyncio.run(scenario())


def test_deadline_stops_work_but_allows_terminal_grace(client):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            await repo.activate(claim, 0.08)
            before = await row(repo, rid)
            assert before.lease_until <= before.execution_deadline_at + timedelta(seconds=10)
            await at(repo, before.execution_deadline_at)
            await repo.heartbeat(claim)
            assert (await row(repo, rid)).lease_until == before.lease_until
            assert await repo.append_delta(claim, "late") is False
            assert (
                await repo.record_tool(
                    claim, "tool.started", {"tool_call_id": "late", "tool_name": "read"}
                )
                is False
            )
            assert await repo.finish(claim, error_code="PROVIDER_TIMEOUT") == "failed"

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["owner", "deadline", "shrink", "cap"])
def test_database_rejects_ownership_or_budget_rebinding(client, field):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            await repo.activate(claim, 1)
            current = await row(repo, rid)
            values = {
                "owner": {"owner_token": uuid4()},
                "deadline": {
                    "execution_deadline_at": current.execution_deadline_at + timedelta(seconds=1)
                },
                "shrink": {"lease_until": current.lease_until - timedelta(seconds=1)},
                "cap": {"lease_until": current.execution_deadline_at + timedelta(seconds=11)},
            }[field]
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(update(Run).where(Run.id == rid).values(**values))
            assert await repo.finish(claim, content="done") == "succeeded"

    asyncio.run(scenario())


def test_expired_owner_cannot_resurrect_and_running_is_never_replayed(client):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.12) as repo:
            claim = await repo.claim(owner, tid, rid)
            await repo.activate(claim, 1)
            current = await row(repo, rid)
            await at(repo, current.lease_until)
            with pytest.raises(OwnershipLost):
                await repo.heartbeat(claim)
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        update(Run)
                        .where(Run.id == rid)
                        .values(lease_until=current.execution_deadline_at)
                    )
            assert await repo.claim(owner, tid, rid) is None
            assert await repo.finish(claim, content="stale") is False
            assert await repo.expire(claim)
            assert not await repo.expire(claim)
            provider = FakeProvider()
            assert await RunExecutor(repo, provider).execute(owner, tid, rid) == "not_claimed"
            assert not provider.requests
            saved = await facts(repo, rid)
            assert saved["error"] == "EXECUTION_EXPIRED"

    asyncio.run(scenario())


def test_cancel_and_heartbeat_race_honors_one_terminal(client):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, tid, rid)
            await repo.activate(claim, 2)
            barrier = asyncio.Barrier(3)

            async def beat():
                await barrier.wait()
                try:
                    await repo.heartbeat(claim)
                except CancellationRequested:
                    pass

            async def cancel():
                await barrier.wait()
                await repo.request_cancel(owner, tid, rid)

            tasks = [asyncio.create_task(beat()), asyncio.create_task(cancel())]
            await barrier.wait()
            await asyncio.gather(*tasks)
            with pytest.raises(CancellationRequested):
                await repo.heartbeat(claim)
            assert await repo.finish(claim, error_code="PROVIDER_ERROR") == "cancelled"
            saved = await facts(repo, rid)
            assert sum(e.type == "run.cancelled" for e in saved["events"]) == 1
            assert saved["error"] is None

    asyncio.run(scenario())


def test_two_claimants_share_no_ownership(client):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            barrier = asyncio.Barrier(3)

            async def claim():
                await barrier.wait()
                return await repo.claim(owner, tid, rid)

            tasks = [asyncio.create_task(claim()), asyncio.create_task(claim())]
            await barrier.wait()
            results = await asyncio.gather(*tasks)
            winners = [c for c in results if c is not None]
            assert len(winners) == 1
            await repo.activate(winners[0], 1)
            stale = replace(winners[0], owner_token=uuid4())
            with pytest.raises(OwnershipLost):
                await repo.heartbeat(stale)
            assert await repo.finish(stale, content="wrong") is False
            assert await repo.finish(winners[0], content="done") == "succeeded"

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
def test_heartbeat_storage_failure_cancels_provider_and_saves_known_failure(
    client, monkeypatch, committed
):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        closed = asyncio.Event()

        async def answer(request):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
            yield

        async with repository() as repo:
            original_heartbeat = repo.heartbeat

            async def unavailable(claim):
                if committed:
                    await original_heartbeat(claim)
                raise OSError("controlled heartbeat storage failure")

            monkeypatch.setattr(repo, "heartbeat", unavailable)
            provider = FakeProvider(answer)
            assert (
                await RunExecutor(
                    repo, provider, cancellation_poll=0.005, heartbeat_seconds=0.02
                ).execute(owner, tid, rid)
                == "failed"
            )
            assert closed.is_set() and len(provider.requests) == 1
            assert (await facts(repo, rid))["error"] == "EXECUTION_STORAGE_UNAVAILABLE"

    asyncio.run(scenario())


def test_activation_ack_loss_never_starts_provider_or_reclaims_budget(client, monkeypatch):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.12) as repo:
            original = repo.activate

            async def committed(claim, seconds):
                await original(claim, seconds)
                raise OSError("controlled activation acknowledgement loss")

            monkeypatch.setattr(repo, "activate", committed)
            provider = FakeProvider()
            with pytest.raises(OSError, match="acknowledgement"):
                await RunExecutor(repo, provider).execute(owner, tid, rid)
            assert not provider.requests
            current = await row(repo, rid)
            assert current.execution_deadline_at is not None
            await at(repo, current.lease_until)
            assert await repo.claim(owner, tid, rid) is None
            assert await repo.expire(
                Claim(owner, tid, rid, current.owner_token, current.lease_until)
            )
            assert (await facts(repo, rid))["error"] == "EXECUTION_EXPIRED"

    asyncio.run(scenario())


def test_terminal_ack_loss_keeps_single_durable_answer(client, monkeypatch):
    _, owner, tid, rid = create_run(client)

    async def scenario():
        async with repository() as repo:
            original = repo.finish

            async def committed(*args, **kwargs):
                await original(*args, **kwargs)
                raise OSError("controlled terminal acknowledgement loss")

            monkeypatch.setattr(repo, "finish", committed)
            provider = FakeProvider()
            with pytest.raises(OSError, match="acknowledgement"):
                await RunExecutor(repo, provider).execute(owner, tid, rid)
            assert len(provider.requests) == 1
            assert await RunExecutor(repo, provider).execute(owner, tid, rid) == "not_claimed"
            saved = await facts(repo, rid)
            assert saved["status"] == "succeeded"
            assert sum(e.type == "message.completed" for e in saved["events"]) == 1
            assert sum(e.type == "run.completed" for e in saved["events"]) == 1

    asyncio.run(scenario())
