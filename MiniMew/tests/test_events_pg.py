"""同一正式执行链的流式分片、可靠失败正文和私有游标查询。"""

import asyncio
import os
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy.exc import IntegrityError
from test_execution_pg import create_run, facts
from test_runs_pg import account, configure_model, submit
from test_runs_pg import client as client

from minimew.conversations import ConversationNotFound
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderDelta, ProviderFailure
from minimew.execution.service import RunExecutor
from minimew.models import Run, RunEventRecord

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


def url(thread, run):
    return f"/v1/threads/{thread}/runs/{run}/events"


def test_live_committed_deltas_empty_page_final_override_and_paginated_replay(client):
    headers, owner, thread, run_id = create_run(client)
    path = url(thread, run_id)

    async def scenario():
        async with repository() as repo:

            async def answer(request):
                yield ProviderDelta("今天")
                assert repo.sessions.kw["bind"].pool.checkedout() == 0
                page = client.get(path, params={"after": 2}, headers=headers)
                assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
                assert [e["data"]["content"] for e in page.json()["items"]] == ["今天"]
                assert page.json()["next_cursor"] == 3
                empty = client.get(path, params={"after": 3}, headers=headers).json()
                assert empty == {"items": [], "next_cursor": 3, "has_more": False}
                assert (await facts(repo, run_id))["status"] == "running"
                yield ProviderDelta("天气很好")
                # 最终正文是权威结果，可校正临时拼接内容。
                yield ProviderCompleted(ModelTurn("text", "今天晴朗。"))

            assert await RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run_id) == (
                "succeeded"
            )
            saved = await facts(repo, run_id)
            assert saved["messages"][1][1:] == ("completed", "今天晴朗。")
            assert [e.type for e in saved["events"]] == [
                "run.created",
                "run.started",
                "message.delta",
                "message.delta",
                "message.completed",
                "run.completed",
            ]

    asyncio.run(scenario())
    received, cursor = [], 0
    while True:
        page = client.get(path, params={"after": cursor, "limit": 1}, headers=headers).json()
        received.extend(page["items"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert [e["sequence"] for e in received] == list(range(1, 7)) and cursor == 6
    assert received[-2]["data"]["content"] == "今天晴朗。"
    assert client.get(path, headers=headers).json()["items"] == received
    assert client.get(path, params={"after": 6}, headers=headers).json() == {
        "items": [],
        "next_cursor": 6,
        "has_more": False,
    }


@pytest.mark.parametrize(
    "mode,code",
    [
        ("typed", "PROVIDER_ERROR"),
        ("unexpected", "PROVIDER_ERROR"),
        ("missing-completed", "PROVIDER_INVALID_RESPONSE"),
        ("empty-delta", "PROVIDER_INVALID_RESPONSE"),
        ("nul-delta", "PROVIDER_INVALID_RESPONSE"),
        ("oversize-delta", "PROVIDER_INVALID_RESPONSE"),
        ("invalid-final", "PROVIDER_INVALID_RESPONSE"),
        ("after-completed", "PROVIDER_INVALID_RESPONSE"),
        ("cleanup-error", "PROVIDER_INVALID_RESPONSE"),
    ],
)
def test_stream_failure_retains_only_committed_prefix_and_final_status(client, mode, code):
    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        closed = asyncio.Event()

        async def answer(request):
            try:
                yield ProviderDelta("今天")
                if mode == "typed":
                    raise ProviderFailure()
                if mode == "unexpected":
                    raise RuntimeError("private-key-and-endpoint")
                if mode in {"empty-delta", "cleanup-error"}:
                    yield ProviderDelta("")
                elif mode == "nul-delta":
                    yield ProviderDelta("bad\x00text")
                elif mode == "oversize-delta":
                    yield ProviderDelta("x" * 4097)
                elif mode == "invalid-final":
                    yield ProviderCompleted(ModelTurn("text", " "))
                elif mode == "after-completed":
                    yield ProviderCompleted(ModelTurn("text", "not yet committed"))
                    yield ProviderDelta("unexpected late text")
            finally:
                closed.set()
                if mode == "cleanup-error":
                    raise RuntimeError("private-cleanup-error")

        async with repository() as repo:
            fake = FakeProvider(answer)
            assert await RunExecutor(repo, fake).execute(owner, thread, run_id) == "failed"
            assert closed.is_set() and len(fake.requests) == 1
            saved = await facts(repo, run_id)
            assert saved["status"] == "failed" and saved["error"] == code
            assert saved["messages"][1][1:] == ("failed", "今天")
            assert saved["events"][-2].data.content == "今天"
            assert saved["events"][-2].data.status == "failed"
            assert saved["events"][-1].type == "run.failed"
            assert "private-key" not in repr(saved)

    asyncio.run(scenario())
    page = client.get(url(thread, run_id), headers=headers)
    assert page.status_code == 200 and "private-key" not in page.text
    history = client.get(f"/v1/threads/{thread}/messages", headers=headers).json()["items"]
    assert history[-1]["content"] == "今天" and history[-1]["status"] == "failed"
    following = UUID(submit(client, headers, str(thread), key="next", content="new").json()["id"])

    async def next_run():
        async with repository() as repo:
            fake = FakeProvider()
            assert await RunExecutor(repo, fake).execute(owner, thread, following) == "succeeded"
            assert [m.content for m in fake.requests[0].messages] == ["new"]

    asyncio.run(next_run())


def test_delta_transaction_failure_rolls_back_sequence_and_does_not_publish_text(
    client, monkeypatch
):
    import minimew.execution.repository as persistence

    headers, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run_id)
            assert await repo.append_delta(claim, "今天")
            original = persistence.append_event

            async def fail_after_insert(session, run, event_type, data):
                await original(session, run, event_type, data)
                await session.flush()
                assert client.get(
                    url(thread, run_id), params={"after": 3}, headers=headers
                ).json() == {
                    "items": [],
                    "next_cursor": 3,
                    "has_more": False,
                }
                raise OSError("injected database failure")

            with monkeypatch.context() as patch:
                patch.setattr(persistence, "append_event", fail_after_insert)
                with pytest.raises(OSError, match="injected"):
                    await repo.append_delta(claim, "不应保留")
            saved = await facts(repo, run_id)
            assert [e.sequence for e in saved["events"]] == [1, 2, 3]
            assert await repo.finish(claim, error_code="PROVIDER_ERROR")
            final = await facts(repo, run_id)
            assert final["messages"][1][1:] == ("failed", "今天")
            assert [e.sequence for e in final["events"]] == [1, 2, 3, 4, 5]

    asyncio.run(scenario())


@pytest.mark.parametrize("swallow", [False, True], ids=["cooperative", "late-delta"])
def test_stream_timeout_retains_prefix_and_rejects_late_delta(client, model_configuration, swallow):
    asyncio.run(
        configure_model(
            "stream-timeout-" + uuid4().hex,
            model_configuration.model_copy(update={"timeout_seconds": 0.2}),
        )
    )
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async def answer(request):
            yield ProviderDelta("已提交")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not swallow:
                    raise
                yield ProviderDelta("迟到正文")
            yield ProviderCompleted(ModelTurn("text", "不得成功"))

        async with repository() as repo:
            assert await RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run_id) == (
                "failed"
            )
            final = await facts(repo, run_id)
            assert final["error"] == "PROVIDER_TIMEOUT"
            assert final["messages"][1][1:] == ("failed", "已提交")

    asyncio.run(scenario())


def test_stream_task_interruption_closes_source_and_retains_prefix(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        async def answer(request):
            try:
                yield ProviderDelta("已保存")
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        async with repository() as repo:
            task = asyncio.create_task(
                RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run_id)
            )
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            final = await facts(repo, run_id)
            assert closed.is_set() and final["error"] == "EXECUTOR_INTERRUPTED"
            assert final["messages"][1][1:] == ("failed", "已保存")

    asyncio.run(scenario())


def test_stream_total_budget_keeps_only_accepted_batches(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async def answer(request):
            for _ in range(25):
                yield ProviderDelta("字" * 4000)
            yield ProviderDelta("超限")
            yield ProviderCompleted(ModelTurn("text", "不得成功"))

        async with repository() as repo:
            assert await RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run_id) == (
                "failed"
            )
            final = await facts(repo, run_id)
            assert final["error"] == "PROVIDER_INVALID_RESPONSE"
            assert final["messages"][1][1:] == ("failed", "字" * 100000)

    asyncio.run(scenario())


def test_database_rejects_terminal_that_discards_committed_prefix(client, monkeypatch):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run_id)
            assert await repo.append_delta(claim, "必须保留")

            async def broken_partial(session, run):
                return ""

            with monkeypatch.context() as patch:
                patch.setattr(repo, "_partial_content", broken_partial)
                with pytest.raises(IntegrityError):
                    await repo.finish(claim, error_code="PROVIDER_ERROR")
            assert (await facts(repo, run_id))["status"] == "running"
            assert await repo.finish(claim, error_code="PROVIDER_ERROR")

    asyncio.run(scenario())


def test_database_error_escapes_executor_then_expiry_preserves_committed_prefix(
    client, monkeypatch
):
    import minimew.execution.repository as persistence

    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.5) as repo:
            original = persistence.append_event

            async def fail_second(session, run, event_type, data):
                if event_type == "message.delta" and data["content"] == "第二批":
                    raise OSError("database unavailable")
                return await original(session, run, event_type, data)

            async def answer(request):
                try:
                    yield ProviderDelta("第一批")
                    yield ProviderDelta("第二批")
                    yield ProviderCompleted(ModelTurn("text", "第一批第二批"))
                finally:
                    raise RuntimeError("private-cleanup-failure-must-not-mask-database")

            with monkeypatch.context() as patch:
                patch.setattr(persistence, "append_event", fail_second)
                with pytest.raises(OSError, match="database unavailable"):
                    await RunExecutor(repo, FakeProvider(answer)).execute(owner, thread, run_id)
            saved = await facts(repo, run_id)
            assert saved["status"] == "running" and saved["error"] is None
            assert saved["events"][-1].data.content == "第一批"
            async with asyncio.timeout(5):
                while not await repo.recover_expired():
                    await asyncio.sleep(0)
            final = await facts(repo, run_id)
            assert final["error"] == "EXECUTION_EXPIRED"
            assert final["messages"][1][1:] == ("failed", "第一批")

    asyncio.run(scenario())


def test_expired_or_wrong_owner_cannot_append_and_recovery_retains_prefix(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.5) as repo:
            claim = await repo.claim(owner, thread, run_id)
            assert await repo.append_delta(claim, "可靠前缀")
            assert not await repo.append_delta(replace(claim, owner_token=uuid4()), "forged")
            with pytest.raises(ConversationNotFound):
                await repo.append_delta(replace(claim, user_id=uuid4()), "foreign")
            async with asyncio.timeout(5):
                while await repo.remaining(claim) > 0:
                    await asyncio.sleep(0)
            assert not await repo.append_delta(claim, "late")
            assert await repo.recover_expired() == 1
            assert not await repo.append_delta(claim, "terminal")
            assert not await repo.finish(claim, content="overwrite")
            final = await facts(repo, run_id)
            assert final["messages"][1][1:] == ("failed", "可靠前缀")
            assert len(final["events"]) == 5

    asyncio.run(scenario())


def test_simultaneous_deltas_are_serial_and_failure_uses_journal_order(client):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run_id)
            assert await asyncio.gather(
                repo.append_delta(claim, "甲"), repo.append_delta(claim, "乙")
            ) == [True, True]
            before = await facts(repo, run_id)
            prefix = "".join(e.data.content for e in before["events"] if e.type == "message.delta")
            assert prefix in {"甲乙", "乙甲"}
            assert await repo.finish(claim, error_code="PROVIDER_TIMEOUT")
            assert (await facts(repo, run_id))["messages"][1][1:] == ("failed", prefix)

    asyncio.run(scenario())


def test_two_streaming_runs_keep_content_and_cursors_isolated(client):
    headers1, owner1, thread1, run1 = create_run(client, content="甲")
    headers2, owner2, thread2, run2 = create_run(client, content="乙")

    async def scenario():
        gate = asyncio.Barrier(2)

        async def answer(request):
            await asyncio.wait_for(gate.wait(), 5)
            text = request.messages[-1].content
            yield ProviderDelta(text)
            yield ProviderCompleted(ModelTurn("text", text + "的回答"))

        async with repository() as repo:
            shared_provider = FakeProvider(answer)
            assert await asyncio.gather(
                RunExecutor(repo, shared_provider).execute(owner1, thread1, run1),
                RunExecutor(repo, shared_provider).execute(owner2, thread2, run2),
            ) == ["succeeded", "succeeded"]

    asyncio.run(scenario())
    for headers, thread, run_id, text in [
        (headers1, thread1, run1, "甲"),
        (headers2, thread2, run2, "乙"),
    ]:
        page = client.get(url(thread, run_id), headers=headers).json()
        assert [e["sequence"] for e in page["items"]] == [1, 2, 3, 4, 5]
        assert all(e["run_id"] == str(run_id) for e in page["items"])
        assert page["items"][2]["data"]["content"] == text
        assert page["items"][3]["data"]["content"] == text + "的回答"


def test_lease_expiring_inside_delta_transaction_rolls_back_entire_batch(client, monkeypatch):
    import minimew.execution.repository as persistence

    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository(lease_seconds=0.5) as repo:
            claim = await repo.claim(owner, thread, run_id)
            assert await repo.append_delta(claim, "此前已提交")
            original = persistence.append_event

            async def expire_after_flush(session, run, event_type, data):
                result = await original(session, run, event_type, data)
                await session.flush()
                async with asyncio.timeout(5):
                    while await repo.remaining(claim) > 0:
                        await asyncio.sleep(0)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(persistence, "append_event", expire_after_flush)
                assert not await repo.append_delta(claim, "事务内到期")
            before = await facts(repo, run_id)
            assert before["status"] == "running" and len(before["events"]) == 3
            assert await repo.recover_expired() == 1
            final = await facts(repo, run_id)
            assert final["messages"][1][1:] == ("failed", "此前已提交")

    asyncio.run(scenario())


def test_event_queries_are_private_run_scoped_and_validate_bounds(client):
    headers, owner, thread, run_id = create_run(client)
    foreign_headers, foreign_thread = account(client)
    foreign_run = submit(client, foreign_headers, foreign_thread).json()["id"]
    path = url(thread, run_id)
    for other in [url(thread, foreign_run), url(foreign_thread, run_id), url(thread, uuid4())]:
        assert client.get(other, headers=headers).status_code == 404
    assert client.get(path, headers=foreign_headers).status_code == 404
    assert client.get(path, headers=foreign_headers, params={"after": 999}).status_code == 404
    assert client.get(path).status_code == 401
    for params in [
        {"after": -1},
        {"after": 2**63},
        {"after": 2},
        {"after": "1.2"},
        {"limit": 0},
        {"limit": 101},
    ]:
        assert client.get(path, headers=headers, params=params).status_code == 422
    assert client.get(path, headers=headers, params={"after": 1}).json() == {
        "items": [],
        "next_cursor": 1,
        "has_more": False,
    }
    assert (
        client.get(url(foreign_thread, foreign_run), headers=foreign_headers).json()["items"][0][
            "sequence"
        ]
        == 1
    )


@pytest.mark.parametrize("mode", ["pending", "foreign-message", "empty", "extra-field"])
def test_database_rejects_invalid_delta_even_when_bypassing_repository(client, mode):
    _, owner, thread, run_id = create_run(client)

    async def scenario():
        async with repository() as repo:
            if mode != "pending":
                await repo.claim(owner, thread, run_id)
            saved = await facts(repo, run_id)
            data = {"message_id": saved["messages"][1][0], "content": "正文"}
            if mode == "foreign-message":
                data["message_id"] = uuid4()
            elif mode == "empty":
                data["content"] = ""
            elif mode == "extra-field":
                data["unexpected"] = "private"
            # 直接写 JSONB，验证数据库保护，绕过 Python Contract 校验。
            data["message_id"] = str(data["message_id"])
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    run = await session.get(Run, run_id, with_for_update=True)
                    run.last_event_sequence += 1
                    session.add(
                        RunEventRecord(
                            event_id=uuid4(),
                            run_id=run_id,
                            thread_id=thread,
                            sequence=run.last_event_sequence,
                            type="message.delta",
                            data=data,
                            timestamp=run.created_at,
                        )
                    )
            assert len((await facts(repo, run_id))["events"]) == len(saved["events"])

    asyncio.run(scenario())
