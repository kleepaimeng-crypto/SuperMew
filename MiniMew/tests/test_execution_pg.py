"""只在可信验收入口创建的临时库中，注入 Provider 调用正式执行链。"""

import asyncio
import json
import os
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from test_runs_pg import account, configure_model, submit
from test_runs_pg import client as client

from minimew.conversations import ConversationNotFound
from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted, ProviderFailure
from minimew.execution.service import RunExecutor
from minimew.generated.run_event_v1 import EVENT_ADAPTER
from minimew.models import Message, Run, RunEventRecord

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def identity(client, headers, thread):
    return UUID(client.get("/auth/me", headers=headers).json()["id"]), UUID(thread)


def create_run(client, *, content="question", key="first"):
    headers, thread = account(client)
    response = submit(client, headers, thread, key=key, content=content)
    assert response.status_code == 201, response.text
    owner, thread_id = identity(client, headers, thread)
    return headers, owner, thread_id, UUID(response.json()["id"])


async def facts(repo, run_id):
    async with repo.sessions() as session:
        run = await session.get(Run, run_id)
        messages = (
            await session.scalars(
                select(Message).where(Message.run_id == run_id).order_by(Message.sequence)
            )
        ).all()
        rows = (
            await session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run_id)
                .order_by(RunEventRecord.sequence)
            )
        ).all()
        events = [
            EVENT_ADAPTER.validate_json(
                json.dumps(
                    {
                        "schema_version": row.schema_version,
                        "event_id": str(row.event_id),
                        "sequence": row.sequence,
                        "run_id": str(row.run_id),
                        "thread_id": str(row.thread_id),
                        "type": row.type,
                        "timestamp": row.timestamp.isoformat(),
                        "data": row.data,
                    }
                )
            )
            for row in rows
        ]
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert run.last_event_sequence == len(events)
        return {
            "status": run.status,
            "error": run.error_code,
            "finished": run.finished_at,
            "messages": [(m.id, m.status, m.content) for m in messages],
            "events": events,
        }


def test_success_same_message_journal_replay_and_public_read(client):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            before = await facts(repo, run_id)
            fake = FakeProvider()
            executor = RunExecutor(repo, fake)
            assert await executor.execute(owner, thread, run_id) == "succeeded"
            assert await executor.execute(owner, thread, run_id) == "not_claimed"
            after = await facts(repo, run_id)
            assert after["status"] == "succeeded" and after["error"] is None
            assert after["finished"] is not None
            assert [m[0] for m in before["messages"]] == [m[0] for m in after["messages"]]
            assert after["messages"][1][1:] == ("completed", "answer:question")
            assert [e.type for e in after["events"]] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.completed",
            ]
            assert after["events"][-2].data.content == "answer:question"
            assert len(fake.requests) == 1
            assert fake.requests[0].snapshot.answer.configuration.thinking.type == "disabled"

    asyncio.run(scenario())
    response = client.get(f"/v1/threads/{thread}/runs/{run_id}", headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "succeeded"
    assert response.json()["finished_at"]
    assert "owner_token" not in response.text and "lease_until" not in response.text
    assert (
        submit(client, headers, str(thread), key="first", content="question").json()
        == response.json()
    )


def test_one_claim_and_thread_order_with_no_database_transaction_during_model_call(client):
    headers, owner, thread, first = create_run(client)
    second = UUID(
        submit(client, headers, str(thread), key="second", content="follow-up").json()["id"]
    )
    third = UUID(submit(client, headers, str(thread), key="third", content="future").json()["id"])

    async def scenario():
        async with repository() as repo:
            entered, release = asyncio.Event(), asyncio.Event()

            async def wait_for_release(request):
                assert repo.sessions.kw["bind"].pool.checkedout() == 0
                entered.set()
                await release.wait()
                yield ProviderCompleted(ModelTurn("text", "first answer"))

            fake = FakeProvider(wait_for_release)
            executor = RunExecutor(repo, fake)
            first_task = asyncio.create_task(executor.execute(owner, thread, first))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert await executor.execute(owner, thread, first) == "not_claimed"
                assert await executor.execute(owner, thread, second) == "not_claimed"
                assert await executor.execute(owner, thread, third) == "not_claimed"
            finally:
                release.set()
            assert await first_task == "succeeded"
            next_fake = FakeProvider()
            assert await RunExecutor(repo, next_fake).execute(owner, thread, second) == "succeeded"
            assert [m.content for m in next_fake.requests[0].messages] == [
                "question",
                "first answer",
                "follow-up",
            ]
            assert (await facts(repo, third))["status"] == "pending"
            assert len(fake.requests) == 1

    asyncio.run(scenario())


def test_two_simultaneous_claims_have_one_winner(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as first, repository() as second:
            claims = await asyncio.gather(
                first.claim(owner, thread, run_id), second.claim(owner, thread, run_id)
            )
            winners = [claim for claim in claims if claim is not None]
            assert len(winners) == 1
            assert await first.finish(winners[0], content="single answer")
            assert len((await facts(first, run_id))["events"]) == 4

    asyncio.run(scenario())


def test_parallel_runs_do_not_share_snapshot_messages_or_result(client, model_configuration):
    _, user1, thread1, run1 = create_run(client, content="one")
    asyncio.run(
        configure_model(
            "isolation-" + uuid4().hex,
            model_configuration.model_copy(update={"model_name": "new-model"}),
        )
    )
    _, user2, thread2, run2 = create_run(client, content="two")

    async def scenario():
        async with repository() as repo:
            gate = asyncio.Barrier(2)

            async def reply(request):
                await asyncio.wait_for(gate.wait(), 5)
                yield ProviderCompleted(
                    ModelTurn(
                        "text",
                        request.snapshot.answer.configuration.model_name
                        + ":"
                        + request.messages[-1].content,
                    )
                )

            fake = FakeProvider(reply)
            outcomes = await asyncio.gather(
                RunExecutor(repo, fake).execute(user1, thread1, run1),
                RunExecutor(repo, fake).execute(user2, thread2, run2),
            )
            assert outcomes == ["succeeded", "succeeded"]
            first, second = await facts(repo, run1), await facts(repo, run2)
            assert first["messages"][1][2] == "test-model-v1:one"
            assert second["messages"][1][2] == "new-model:two"
            assert all(e.run_id == run1 and e.thread_id == thread1 for e in first["events"])
            assert all(e.run_id == run2 and e.thread_id == thread2 for e in second["events"])

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reply,code",
    [
        (ProviderFailure("PROVIDER_ERROR"), "PROVIDER_ERROR"),
        (RuntimeError("private-api-key-and-endpoint"), "PROVIDER_ERROR"),
        ("", "PROVIDER_INVALID_RESPONSE"),
        (None, "PROVIDER_INVALID_RESPONSE"),
    ],
    ids=["typed", "unexpected", "empty", "wrong-type"],
)
def test_provider_failure_is_persistent_and_sanitized(client, reply, code):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:

            async def generate(request):
                if isinstance(reply, Exception):
                    raise reply
                yield ProviderCompleted(ModelTurn("text", reply))

            fake = FakeProvider(generate)
            assert await RunExecutor(repo, fake).execute(owner, thread, run_id) == "failed"
            saved = await facts(repo, run_id)
            assert saved["status"] == "failed" and saved["error"] == code
            assert saved["messages"][0][1:] == ("completed", "question")
            assert saved["messages"][1][1:] == ("failed", "")
            assert saved["events"][-1].data.error_code == code
            assert "private-api-key" not in repr(saved)
            assert len(fake.requests) == 1

    asyncio.run(scenario())
    assert (
        client.get(f"/v1/threads/{thread}/runs/{run_id}", headers=headers).json()["error_code"]
        == code
    )


def test_tool_turn_cannot_complete_ordinary_run_or_publish_intermediate_text(client):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            before = await facts(repo, run_id)

            async def generate(request):
                assert request.tools == ()
                yield ProviderCompleted(
                    ModelTurn(
                        "tool_calls",
                        "先查知识库",
                        (ToolCall("call_1", "knowledge_search", '{"query":"餐费"}'),),
                    )
                )

            fake = FakeProvider(generate)
            assert await RunExecutor(repo, fake).execute(owner, thread, run_id) == "failed"
            after = await facts(repo, run_id)
            assert after["status"] == "failed" and after["error"] == "PROVIDER_INVALID_RESPONSE"
            assert [m[0] for m in after["messages"]] == [m[0] for m in before["messages"]]
            assert after["messages"][1][1:] == ("failed", "")
            assert [event.type for event in after["events"]] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.failed",
            ]
            assert after["events"][-2].data.content == ""
            assert len(fake.requests) == 1

    asyncio.run(scenario())
    assert (
        client.get(f"/v1/threads/{thread}/runs/{run_id}", headers=headers).json()["status"]
        == "failed"
    )


@pytest.mark.parametrize("swallow", [False, True], ids=["cooperative", "late-return"])
def test_timeout_and_provider_swallowing_cancellation_cannot_report_success(
    client, model_configuration, swallow
):
    asyncio.run(
        configure_model(
            "timeout-" + uuid4().hex,
            model_configuration.model_copy(update={"timeout_seconds": 0.03}),
        )
    )
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:

            async def swallow_cancel(request):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if not swallow:
                        raise
                    yield ProviderCompleted(ModelTurn("text", "late answer"))

            fake = FakeProvider(swallow_cancel)
            assert await RunExecutor(repo, fake).execute(owner, thread, run_id) == "failed"
            saved = await facts(repo, run_id)
            assert saved["error"] == "PROVIDER_TIMEOUT"
            assert saved["messages"][1][2] == "" and len(fake.requests) == 1

    asyncio.run(scenario())


def test_expired_owner_recovery_and_duplicate_finish(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.03) as repo:
            claim = await repo.claim(owner, thread, run_id)
            assert claim is not None
            # 根据数据库时间等待实际过期，不靠固定 sleep 猜测调度。
            async with asyncio.timeout(5):
                while await repo.remaining(claim) > 0:
                    await asyncio.sleep(0)
            results = await asyncio.gather(repo.recover_expired(), repo.recover_expired())
            assert sum(results) == 1
            assert not await repo.finish(claim, content="late answer")
            assert not await repo.expire(claim)
            saved = await facts(repo, run_id)
            assert saved["status"] == "failed" and saved["error"] == "EXECUTION_EXPIRED"
            assert saved["messages"][1][2] == "" and len(saved["events"]) == 4

    asyncio.run(scenario())


def test_wrong_owner_token_and_foreign_user_cannot_execute_or_finish(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            with pytest.raises(ConversationNotFound):
                await repo.claim(uuid4(), thread, run_id)
            claim = await repo.claim(owner, thread, run_id)
            assert claim
            assert not await repo.finish(replace(claim, owner_token=uuid4()), content="forged")
            assert (await facts(repo, run_id))["status"] == "running"
            assert await repo.finish(claim, content="real answer")
            assert not await repo.finish(claim, content="overwrite")

    asyncio.run(scenario())


def test_finish_event_write_failure_rolls_back_all_terminal_facts(client, monkeypatch):
    import minimew.execution.repository as persistence

    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run_id)
            original = persistence.append_event

            async def fail_after_event(session, run, event_type, data):
                event = await original(session, run, event_type, data)
                if event_type == "run.completed":
                    await session.flush()
                    raise RuntimeError("injected event transaction failure")
                return event

            with monkeypatch.context() as patch:
                patch.setattr(persistence, "append_event", fail_after_event)
                with pytest.raises(RuntimeError, match="injected"):
                    await repo.finish(claim, content="in-memory answer")
            saved = await facts(repo, run_id)
            assert saved["status"] == "running" and saved["finished"] is None
            assert saved["messages"][1][1:] == ("pending", "")
            assert [event.type for event in saved["events"]] == ["run.created", "run.started"]
            # 只重试保存同一内存结果，不重新调用模型。
            assert await repo.finish(claim, content="in-memory answer")

    asyncio.run(scenario())


def test_database_rejects_partial_terminal_commit_and_journal_mutation(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run_id)
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        update(Run)
                        .where(Run.id == run_id)
                        .values(status="succeeded", finished_at=func.clock_timestamp())
                    )
            assert (await facts(repo, run_id))["status"] == "running"
            assert await repo.finish(claim, content="answer")
            for statement in [
                update(Run).where(Run.id == run_id).values(status="running", finished_at=None),
                update(RunEventRecord).where(RunEventRecord.run_id == run_id).values(data={}),
                update(Message).where(Message.run_id == run_id).values(content="rewritten"),
                delete(Message).where(Message.run_id == run_id),
                delete(RunEventRecord).where(RunEventRecord.run_id == run_id),
            ]:
                with pytest.raises(IntegrityError):
                    async with repo.sessions() as session, session.begin():
                        await session.execute(statement)

    asyncio.run(scenario())


def test_failed_turn_is_retained_but_not_used_as_model_history(client):
    headers, owner, thread, first = create_run(client, content="failed question")
    second = UUID(
        submit(client, headers, str(thread), key="second", content="new question").json()["id"]
    )

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, first)
            assert await repo.finish(claim, error_code="PROVIDER_ERROR")
            fake = FakeProvider()
            assert await RunExecutor(repo, fake).execute(owner, thread, second) == "succeeded"
            assert [m.content for m in fake.requests[0].messages] == ["new question"]
            assert (await facts(repo, first))["messages"][0][2] == "failed question"

    asyncio.run(scenario())


def test_executor_task_interruption_is_not_user_cancellation(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            entered = asyncio.Event()

            async def wait(request):
                entered.set()
                await asyncio.Event().wait()
                yield ProviderCompleted(ModelTurn("text", "unreachable"))

            task = asyncio.create_task(
                RunExecutor(repo, FakeProvider(wait)).execute(owner, thread, run_id)
            )
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await facts(repo, run_id))["error"] == "EXECUTOR_INTERRUPTED"

    asyncio.run(scenario())


def test_late_provider_cannot_overwrite_recovered_expired_run(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.5) as repo:
            entered, release = asyncio.Event(), asyncio.Event()

            async def late(request):
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                yield ProviderCompleted(ModelTurn("text", "late answer must not persist"))

            task = asyncio.create_task(
                RunExecutor(repo, FakeProvider(late)).execute(owner, thread, run_id)
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                async with asyncio.timeout(5):
                    while not await repo.recover_expired():
                        await asyncio.sleep(0)
            finally:
                release.set()
            assert await task == "ownership_lost"
            saved = await facts(repo, run_id)
            assert saved["error"] == "EXECUTION_EXPIRED"
            assert saved["messages"][1][2] == ""
            assert len(saved["events"]) == 4

    asyncio.run(scenario())
