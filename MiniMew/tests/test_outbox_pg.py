"""临时 PostgreSQL：本地原子性、发送资格和独立确认事务。"""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from execution_helpers import FakeProvider, repository
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_execution_pg import create_run, facts
from test_runs_pg import client as client

from minimew.execution.notifications import NotificationUnavailable
from minimew.execution.outbox import OutboxPublisher
from minimew.execution.service import RunExecutor
from minimew.models import EventOutbox, RunEventRecord

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


async def clear_previous_outbox(repo):
    # 仅验收脚本创建的临时库。清理此前用例待办，业务 Event/Run 不动。
    assert os.environ["MINIMEW_DB_NAME"].startswith("minimew_test_")
    async with repo.sessions() as session, session.begin():
        await session.execute(
            update(EventOutbox)
            .where(EventOutbox.published_at.is_(None))
            .values(published_at=func.clock_timestamp())
        )
        await session.execute(text("DELETE FROM event_outbox"))


async def rows_for(repo, run_id):
    async with repo.sessions() as session:
        return list(
            (
                await session.scalars(
                    select(EventOutbox).join(RunEventRecord).where(RunEventRecord.run_id == run_id)
                )
            ).all()
        )


@pytest.fixture(autouse=True)
def isolated_outbox(client):
    async def clean():
        async with repository() as repo:
            await clear_previous_outbox(repo)

    asyncio.run(clean())


def test_outbox_insert_failure_rolls_back_claim_and_event(client, monkeypatch):
    import minimew.execution.journal as journal

    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            before = await facts(repo, run)
            event_id = before["events"][0].event_id
            monkeypatch.setattr(
                journal, "EventOutbox", lambda **kwargs: EventOutbox(event_id=event_id)
            )
            with pytest.raises(IntegrityError):
                await repo.claim(owner, thread, run)
            after = await facts(repo, run)
            assert after["status"] == "pending" and after["events"] == before["events"]
            assert len(await rows_for(repo, run)) == 1

    asyncio.run(scenario())


def test_database_rejects_new_event_without_outbox_and_pending_delete(client):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            claim = await repo.claim(owner, thread, run)
            assert claim is not None
            with pytest.raises(IntegrityError) as error:
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        text("""
                        INSERT INTO run_events
                            (event_id,run_id,thread_id,sequence,type,timestamp,data)
                        SELECT gen_random_uuid(),run_id,thread_id,3,'message.delta',
                        clock_timestamp(),
                        jsonb_build_object('message_id',id::text,'content','x')
                        FROM messages WHERE run_id=:run AND role='assistant'
                    """),
                        {"run": run},
                    )
                    await session.execute(
                        text("UPDATE runs SET last_event_sequence=3 WHERE id=:run"), {"run": run}
                    )
            assert error.value.orig.sqlstate == "23514"
            assert len((await facts(repo, run))["events"]) == 2
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        text("DELETE FROM event_outbox WHERE event_id=:event"),
                        {"event": (await facts(repo, run))["events"][0].event_id},
                    )

    asyncio.run(scenario())


def test_publish_has_no_database_transaction_and_does_not_change_business_facts(client):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            assert (
                await RunExecutor(repo, FakeProvider()).execute(owner, thread, run) == "succeeded"
            )
            before = await facts(repo, run)
            observed = []

            class Transport:
                async def publish(self, event):
                    assert repo.sessions.kw["bind"].pool.checkedout() == 0
                    observed.append(event)

            publisher = OutboxPublisher(repo.sessions, Transport())
            assert await publisher.publish_once() == 4
            assert [event.type for event in observed] == [
                "run.created",
                "run.started",
                "message.completed",
                "run.completed",
            ]
            saved = await rows_for(repo, run)
            assert all(row.published_at is not None and row.attempts == 1 for row in saved)
            assert await publisher.publish_once() == 0
            assert await facts(repo, run) == before

    asyncio.run(scenario())


def test_failed_send_retains_event_and_waits_until_next_attempt(client):
    _, _, _, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            transport = type(
                "Transport", (), {"publish": AsyncMock(side_effect=NotificationUnavailable)}
            )()
            publisher = OutboxPublisher(repo.sessions, transport)
            with pytest.raises(NotificationUnavailable):
                await publisher.publish_once()
            rows = await rows_for(repo, run)
            assert rows[0].published_at is None and rows[0].attempts == 1
            assert rows[0].last_error_code == "REDIS_UNAVAILABLE"
            assert rows[0].event_id not in [item.event_id for item in await publisher.read_due()]
            assert (await facts(repo, run))["status"] == "pending"

    asyncio.run(scenario())


def test_mark_failure_retries_same_event_and_published_row_cannot_be_reopened(client, monkeypatch):
    _, owner, thread, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            assert await repo.claim(owner, thread, run) is not None
            sent = []

            class Transport:
                async def publish(self, event):
                    sent.append(event.event_id)

            first = OutboxPublisher(repo.sessions, Transport())
            monkeypatch.setattr(
                first, "record_result", AsyncMock(side_effect=SQLAlchemyError("private"))
            )
            with pytest.raises(SQLAlchemyError):
                await first.publish_once()
            assert (await rows_for(repo, run))[0].published_at is None
            restarted = OutboxPublisher(repo.sessions, Transport())
            assert await restarted.publish_once() == 2
            assert sent[0] == sent[1] and len((await facts(repo, run))["events"]) == 2
            with pytest.raises(IntegrityError):
                async with repo.sessions() as session, session.begin():
                    await session.execute(
                        update(EventOutbox)
                        .where(EventOutbox.event_id == sent[0])
                        .values(published_at=None)
                    )

    asyncio.run(scenario())


def test_two_publishers_cannot_reopen_or_duplicate_committed_event(client):
    _, _, _, run = create_run(client)

    async def scenario():
        async with repository() as repo:
            arrived = 0
            both = asyncio.Event()

            class Transport:
                async def publish(self, event):
                    nonlocal arrived
                    arrived += 1
                    if arrived == 2:
                        # 两个读取事务均结束后、任一确认事务开始前观察。
                        assert repo.sessions.kw["bind"].pool.checkedout() == 0
                        both.set()
                    await both.wait()

            one = OutboxPublisher(repo.sessions, Transport())
            two = OutboxPublisher(repo.sessions, Transport())
            original = (await one.read_due())[0]
            assert await asyncio.wait_for(
                asyncio.gather(one.publish_once(), two.publish_once()), 3
            ) == [1, 1]
            saved = (await rows_for(repo, run))[0]
            assert saved.published_at is not None and saved.attempts == 1
            await one.record_result(original, "REDIS_UNAVAILABLE")
            final = (await rows_for(repo, run))[0]
            assert final.published_at == saved.published_at and final.last_error_code is None
            assert len((await facts(repo, run))["events"]) == 1

    asyncio.run(scenario())
