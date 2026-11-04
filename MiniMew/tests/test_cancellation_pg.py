"""取消沿原 Run/Journal 事务；只使用显式临时 PostgreSQL。"""

import asyncio
import os
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_deepseek import transport as transport
from test_execution_pg import create_run, facts
from test_runs_pg import account, submit
from test_runs_pg import client as client

from minimew.execution.repository import CancellationRequested
from minimew.execution.service import RunExecutor
from minimew.models import EventOutbox, Message, Run, RunEventRecord

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit temporary PostgreSQL required",
)


def cancel(client, headers, thread, run):
    return client.post(f"/v1/threads/{thread}/runs/{run}/cancel", headers=headers)


def test_pending_cancel_atomic_pair_no_execution_idempotency_and_replay(client):
    headers, owner, thread, run = create_run(client)
    response = cancel(client, headers, thread, run)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancel_requested_at"] and response.json()["finished_at"]
    assert cancel(client, headers, thread, run).json() == response.json()
    replay = submit(client, headers, str(thread), key="first", content="question")
    assert replay.status_code == 200 and replay.json() == response.json()

    async def scenario():
        async with repository() as repo:
            provider = FakeProvider()
            assert await RunExecutor(repo, provider).execute(owner, thread, run) == "not_claimed"
            assert not provider.requests
            saved = await facts(repo, run)
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.cancel_requested",
                "message.completed",
                "run.cancelled",
            ]
            assert saved["messages"][0][1:] == ("completed", "question")
            assert saved["messages"][1][1:] == ("cancelled", "")
            assert saved["error"] is None
            async with repo.sessions() as session:
                row = await session.get(Run, run)
                assert row.owner_token is row.started_at is row.lease_until is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EventOutbox)
                        .where(EventOutbox.event_id.in_(e.event_id for e in saved["events"]))
                    )
                    == 4
                )

    asyncio.run(scenario())


def test_running_cancel_keeps_prefix_and_overrides_in_memory_completion(client):
    headers, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run)
            assert await repo.append_delta(claim, "今天")
            response, pending = await repo.request_cancel(owner, thread, run)
            assert pending and response.status == "running"
            again, pending = await repo.request_cancel(owner, thread, run)
            assert pending and again.cancel_requested_at == response.cancel_requested_at
            with pytest.raises(CancellationRequested):
                await repo.append_delta(claim, "未提交")
            assert await repo.finish(claim, content="今天很好，仅存在模型内存") == "cancelled"
            assert not await repo.finish(claim, content="late")
            saved = await facts(repo, run)
            assert saved["messages"][1][1:] == ("cancelled", "今天")
            assert [e.type for e in saved["events"]][-3:] == [
                "run.cancel_requested",
                "message.completed",
                "run.cancelled",
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("late", [False, True])
def test_no_new_output_cancel_stops_reading_even_if_provider_returns_late(client, late):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            entered, closed = asyncio.Event(), asyncio.Event()

            async def answer(request):
                from minimew.execution.provider import ProviderCompleted, ProviderDelta

                yield ProviderDelta("prefix")
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if late:
                        yield ProviderCompleted("late answer")
                    else:
                        raise
                finally:
                    closed.set()

            provider = FakeProvider(answer)
            task = asyncio.create_task(
                RunExecutor(repo, provider, cancellation_poll=0.01).execute(
                    owner,
                    thread,
                    run,
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            await repo.request_cancel(owner, thread, run)
            assert await asyncio.wait_for(task, 5) == "cancelled"
            assert closed.is_set() and len(provider.requests) == 1
            assert (await facts(repo, run))["messages"][1][1:] == ("cancelled", "prefix")

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [None, "PROVIDER_ERROR"])
def test_already_terminal_is_immutable_after_cancel(client, error):
    headers, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run)
            assert await repo.finish(claim, content="answer", error_code=error)
            return await facts(repo, run)

    before = asyncio.run(scenario())
    response = cancel(client, headers, thread, run)
    assert response.status_code == 200 and response.json()["status"] == before["status"]
    assert response.json()["cancel_requested_at"] is None
    asyncio.run(assert_facts(run, before))


async def assert_facts(run, expected):
    async with repository() as repo:
        assert await facts(repo, run) == expected


def test_cancel_requires_identity_and_owned_thread_run(client):
    headers, _, thread, run = create_run(client)
    foreign, other = account(client)
    assert cancel(client, {}, thread, run).status_code == 401
    for auth, target in [(foreign, thread), (headers, other), (headers, uuid4())]:
        assert cancel(client, auth, target, run).status_code == 404
    response = cancel(client, headers, thread, run)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("committed", [False, True])
def test_503_does_not_assert_rollback_and_retry_same_run_is_safe(client, monkeypatch, committed):
    from minimew.execution.repository import ExecutionRepository

    headers, _, thread, run = create_run(client)
    original = ExecutionRepository.request_cancel

    async def broken(self, *args):
        if committed:
            await original(self, *args)
        raise SQLAlchemyError("private database details")

    with monkeypatch.context() as patch:
        patch.setattr(ExecutionRepository, "request_cancel", broken)
        response = cancel(client, headers, thread, run)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "RUN_CANCELLATION_UNAVAILABLE"}}
    current = client.get(f"/v1/threads/{thread}/runs/{run}", headers=headers).json()
    assert current["status"] == ("cancelled" if committed else "pending")
    assert cancel(client, headers, thread, run).status_code == 200


def test_concurrent_repeat_cancel_only_one_request_and_terminal(client):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            results = await asyncio.gather(
                *[repo.request_cancel(owner, thread, run) for _ in range(3)]
            )
            assert all(result[0].status == "cancelled" and not result[1] for result in results)
            assert len((await facts(repo, run))["events"]) == 4

    asyncio.run(scenario())


def test_cancelled_round_is_excluded_and_next_same_thread_can_execute(client):
    headers, owner, thread, run = create_run(client)
    second = submit(client, headers, str(thread), key="second", content="next").json()
    assert cancel(client, headers, thread, run).status_code == 200

    async def scenario():
        from uuid import UUID

        async with repository() as repo:
            fake = FakeProvider()
            assert (
                await RunExecutor(repo, fake).execute(owner, thread, UUID(second["id"]))
                == "succeeded"
            )
            assert [m.content for m in fake.requests[0].messages] == ["next"]

    asyncio.run(scenario())


def test_expired_owner_cannot_cancel_but_recovery_honors_request(client):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.2) as repo:
            claim = await repo.claim(owner, thread, run)
            assert await repo.append_delta(claim, "prefix")
            await repo.request_cancel(owner, thread, run)
            async with asyncio.timeout(5):
                while await repo.remaining(claim) > 0:
                    await asyncio.sleep(0.01)
            assert not await repo.finish(claim, error_code="EXECUTOR_INTERRUPTED")
            results = await asyncio.gather(repo.recover_expired(), repo.recover_expired())
            assert sum(results) == 1
            saved = await facts(repo, run)
            assert saved["status"] == "cancelled" and saved["error"] is None
            assert saved["messages"][1][1:] == ("cancelled", "prefix")
            assert not await repo.finish(replace(claim, owner_token=uuid4()), content="late")

    asyncio.run(scenario())


def test_cancel_transaction_failure_rolls_back_request_message_events_and_outbox(
    client, monkeypatch
):
    import minimew.execution.repository as persistence

    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            original = persistence.append_event

            async def broken(session, row, kind, data):
                await original(session, row, kind, data)
                if kind == "run.cancelled":
                    raise RuntimeError("injected")

            before = await facts(repo, run)
            with monkeypatch.context() as patch:
                patch.setattr(persistence, "append_event", broken)
                with pytest.raises(RuntimeError, match="injected"):
                    await repo.request_cancel(owner, thread, run)
            assert await facts(repo, run) == before
            async with repo.sessions() as session:
                assert (await session.get(Run, run)).cancel_requested_at is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EventOutbox)
                        .join(
                            RunEventRecord,
                            EventOutbox.event_id == RunEventRecord.event_id,
                        )
                        .where(RunEventRecord.run_id == run)
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_cancelled_message_and_run_are_immutable(client):
    headers, _, thread, run = create_run(client)
    assert cancel(client, headers, thread, run).status_code == 200

    async def scenario():
        async with repository() as repo:
            for statement in [
                update(Run).where(Run.id == run).values(cancel_requested_at=None),
                update(Run).where(Run.id == run).values(status="pending", finished_at=None),
                update(Message)
                .where(Message.run_id == run, Message.role == "assistant")
                .values(
                    content="overwrite",
                ),
            ]:
                with pytest.raises(IntegrityError):
                    async with repo.sessions() as session, session.begin():
                        await session.execute(statement)

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["cancel", "finish"])
def test_cancel_finish_race_uses_locked_commit_order(client, monkeypatch, first):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run)
            locked, release = asyncio.Event(), asyncio.Event()
            original = repo._locked_run

            async def held(*args):
                row = await original(*args)
                if asyncio.current_task().get_name() == "first-commit":
                    locked.set()
                    await release.wait()
                return row

            monkeypatch.setattr(repo, "_locked_run", held)
            operations = {
                "cancel": lambda: repo.request_cancel(owner, thread, run),
                "finish": lambda: repo.finish(claim, content="answer"),
            }
            first_task = asyncio.create_task(operations[first](), name="first-commit")
            await asyncio.wait_for(locked.wait(), 5)
            second_task = asyncio.create_task(
                operations["finish" if first == "cancel" else "cancel"]()
            )
            release.set()
            await asyncio.gather(first_task, second_task)
            result = await facts(repo, run)
            assert result["status"] == ("cancelled" if first == "cancel" else "succeeded")
            assert (
                sum(
                    e.type in {"run.completed", "run.failed", "run.cancelled"}
                    for e in result["events"]
                )
                == 1
            )
            assert (result["messages"][1][2] == "") == (first == "cancel")

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["cancel", "claim"])
def test_pending_cancel_claim_race_rechecks_candidate(client, monkeypatch, first):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            assert run in [row[2] for row in await repo.candidates(limit=100)]
            locked, release = asyncio.Event(), asyncio.Event()
            original = repo._locked_run

            async def held(*args):
                row = await original(*args)
                if asyncio.current_task().get_name() == "first-commit":
                    locked.set()
                    await release.wait()
                return row

            monkeypatch.setattr(repo, "_locked_run", held)
            operations = {
                "cancel": lambda: repo.request_cancel(owner, thread, run),
                "claim": lambda: repo.claim(owner, thread, run),
            }
            first_task = asyncio.create_task(operations[first](), name="first-commit")
            await asyncio.wait_for(locked.wait(), 5)
            other = "claim" if first == "cancel" else "cancel"
            second_task = asyncio.create_task(operations[other]())
            release.set()
            a, b = await asyncio.gather(first_task, second_task)
            claim = a if first == "claim" else b
            if first == "cancel":
                assert claim is None
            else:
                assert claim is not None
                assert await repo.finish(claim, error_code="EXECUTOR_INTERRUPTED") == "cancelled"
            assert (await facts(repo, run))["status"] == "cancelled"

    asyncio.run(scenario())


def test_cancel_during_backoff_never_starts_second_model_attempt(client):
    from minimew.execution.provider import ProviderCompleted, ProviderFailure

    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            failed = asyncio.Event()

            async def answer(data):
                failed.set()
                raise ProviderFailure(retryable=True)
                yield ProviderCompleted("never")

            provider = FakeProvider(answer)
            task = asyncio.create_task(
                RunExecutor(repo, provider, cancellation_poll=0.01).execute(
                    owner,
                    thread,
                    run,
                )
            )
            await asyncio.wait_for(failed.wait(), 5)
            await repo.request_cancel(owner, thread, run)
            assert await asyncio.wait_for(task, 5) == "cancelled"
            assert len(provider.requests) == 1

    asyncio.run(scenario())


def test_database_guards_cancel_request_event_and_late_delta(client):
    import minimew.execution.repository as persistence

    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run)
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        update(Run)
                        .where(Run.id == run)
                        .values(
                            cancel_requested_at=func.clock_timestamp(),
                        )
                    )
            assert await repo.append_delta(claim, "prefix")
            await repo.request_cancel(owner, thread, run)
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    row = await repo._locked_run(session, owner, thread, run)
                    answer = (await persistence.run_messages(session, row))[1]
                    await persistence.append_event(
                        session,
                        row,
                        "message.delta",
                        {
                            "message_id": answer.id,
                            "content": "late",
                        },
                    )
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        update(Run)
                        .where(Run.id == run)
                        .values(
                            cancel_requested_at=func.clock_timestamp(),
                        )
                    )
            assert await repo.finish(claim, content="ignored") == "cancelled"
            assert (await facts(repo, run))["messages"][1][2] == "prefix"

    asyncio.run(scenario())


def test_database_rejects_cancel_terminal_discarding_committed_prefix(client, monkeypatch):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run)
            assert await repo.append_delta(claim, "prefix")
            await repo.request_cancel(owner, thread, run)
            before = await facts(repo, run)
            with monkeypatch.context() as patch:
                patch.setattr(repo, "_partial_content", AsyncMock(return_value=""))
                with pytest.raises(IntegrityError):
                    await repo.finish(claim, error_code="PROVIDER_ERROR")
            assert await facts(repo, run) == before
            assert await repo.finish(claim, error_code="PROVIDER_ERROR") == "cancelled"

    asyncio.run(scenario())


def test_real_sdk_consumer_cancel_closes_clients_in_own_task(
    client,
    transport,
    model_configuration,
):
    import json

    import httpx2
    from pydantic import SecretStr
    from test_deepseek import chunk
    from test_runs_pg import configure_model

    from minimew.execution.deepseek import DeepSeekProvider

    asyncio.run(
        configure_model(
            "sdk-cancel-" + uuid4().hex,
            model_configuration.model_copy(
                update={
                    "base_url": "https://api.deepseek.com",
                    "model_name": "deepseek-v4-flash",
                }
            ),
        )
    )
    _, owner, thread, run = create_run(client)

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        class WaitingStream(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield b"data: " + json.dumps(chunk("SDK prefix")).encode() + b"\n\n"
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        async def response(request):
            assert json.loads(request.content)["thinking"] == {"type": "disabled"}
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=WaitingStream()
            )

        requests, clients = transport(response)
        async with repository() as repo:
            task = asyncio.create_task(
                RunExecutor(
                    repo,
                    DeepSeekProvider(SecretStr("test-key")),
                    cancellation_poll=0.01,
                ).execute(owner, thread, run)
            )
            await asyncio.wait_for(entered.wait(), 5)
            await repo.request_cancel(owner, thread, run)
            assert await asyncio.wait_for(task, 5) == "cancelled"
            assert closed.is_set() and all(c.is_closed for c in clients)
            assert len(requests) == 1
            assert (await facts(repo, run))["messages"][1][1:] == ("cancelled", "SDK prefix")

    asyncio.run(scenario())


@pytest.mark.parametrize("requested", [False, True])
def test_monitor_storage_failure_saves_interruption_or_prior_cancel(client, monkeypatch, requested):
    from minimew.execution.provider import ProviderDelta

    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            entered, fail, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
            original = repo.check_control

            async def control(claim):
                if asyncio.current_task().get_name().startswith("minimew-cancel-monitor-"):
                    await fail.wait()
                    raise SQLAlchemyError("injected monitor database failure")
                return await original(claim)

            monkeypatch.setattr(repo, "check_control", control)

            async def answer(data):
                yield ProviderDelta("prefix")
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

            fake = FakeProvider(answer)
            task = asyncio.create_task(
                RunExecutor(repo, fake, cancellation_poll=0.01).execute(owner, thread, run)
            )
            await asyncio.wait_for(entered.wait(), 5)
            if requested:
                await repo.request_cancel(owner, thread, run)
            fail.set()
            assert await asyncio.wait_for(task, 5) == ("cancelled" if requested else "failed")
            result = await facts(repo, run)
            assert result["error"] == (None if requested else "EXECUTION_STORAGE_UNAVAILABLE")
            assert result["messages"][1][2] == "prefix"
            assert closed.is_set() and len(fake.requests) == 1

    asyncio.run(scenario())


def test_monitor_and_final_storage_failure_preserves_running_until_expiry(client, monkeypatch):
    from minimew.execution.provider import ProviderDelta

    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.5) as repo:
            entered, fail = asyncio.Event(), asyncio.Event()
            original = repo.check_control

            async def control(claim):
                if asyncio.current_task().get_name().startswith("minimew-cancel-monitor-"):
                    await fail.wait()
                    raise SQLAlchemyError("read failure")
                return await original(claim)

            async def answer(data):
                yield ProviderDelta("prefix")
                entered.set()
                await asyncio.Event().wait()

            with monkeypatch.context() as patch:
                patch.setattr(repo, "check_control", control)
                patch.setattr(
                    repo, "finish", AsyncMock(side_effect=SQLAlchemyError("save failure"))
                )
                fake = FakeProvider(answer)
                task = asyncio.create_task(
                    RunExecutor(repo, fake, cancellation_poll=0.01).execute(owner, thread, run)
                )
                await asyncio.wait_for(entered.wait(), 5)
                await repo.request_cancel(owner, thread, run)
                fail.set()
                with pytest.raises(SQLAlchemyError, match="save failure"):
                    await task
                assert repo.finish.await_count == 1 and len(fake.requests) == 1
            assert (await facts(repo, run))["status"] == "running"
            async with asyncio.timeout(5):
                while not await repo.recover_expired():
                    await asyncio.sleep(0.01)
            assert (await facts(repo, run))["status"] == "cancelled"

    asyncio.run(scenario())
