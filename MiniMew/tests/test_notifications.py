"""通知协议、失败等待及应用资源；默认不访问 Redis/PostgreSQL。"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from test_sse import event, reading, subscription

from minimew.execution.notifications import NotificationUnavailable, RedisNotifications
from minimew.execution.outbox import notification_lifespan
from minimew.settings import Settings


@pytest.mark.parametrize(
    "variable,value",
    [
        ("MINIMEW_REDIS_PORT", "0"),
        ("MINIMEW_REDIS_KEY_PREFIX", "other-project"),
        ("MINIMEW_EVENT_POLL_SECONDS", "nan"),
        ("MINIMEW_REDIS_STREAM_MAXLEN", "0"),
    ],
)
def test_notification_settings_reject_invalid_bounds(monkeypatch, variable, value):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-only")
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        Settings()


def test_redis_secret_is_required_and_redacted(monkeypatch):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-only")
    monkeypatch.setenv("MINIMEW_REDIS_PASSWORD", "independent-private-redis-password")
    assert "independent-private" not in repr(Settings())
    monkeypatch.delenv("MINIMEW_REDIS_PASSWORD")
    with pytest.raises(ValidationError):
        Settings()


def test_transport_has_separate_bounded_pools_and_no_implicit_retries():
    async def scenario():
        settings = Settings(db_password="offline-only", event_poll_seconds=30)
        transport = RedisNotifications(settings)
        try:
            assert transport.writer.connection_pool is not transport.reader.connection_pool
            assert transport.writer.connection_pool.max_connections == 4
            assert transport.reader.connection_pool.max_connections == 32
            assert (
                transport.reader.connection_pool.connection_kwargs["socket_timeout"]
                > settings.event_poll_seconds
            )
            for client in (transport.writer, transport.reader):
                assert client.connection_pool.connection_kwargs["retry"].get_retries() == 0
        finally:
            await transport.aclose()

    asyncio.run(scenario())


def test_transport_errors_are_typed_and_do_not_expose_original_exception(monkeypatch):
    import minimew.execution.notifications as notifications

    async def scenario():
        writer, reader = MagicMock(), MagicMock()
        writer.eval = AsyncMock(side_effect=RedisConnectionError("private-host-and-password"))
        reader.xread = AsyncMock(side_effect=RedisConnectionError("private-host-and-password"))
        monkeypatch.setattr(notifications, "Redis", MagicMock(side_effect=[writer, reader]))
        transport = RedisNotifications(Settings(db_password="offline-only"))
        with pytest.raises(NotificationUnavailable) as error:
            await transport.publish(event(1))
        assert str(error.value) == "REDIS_UNAVAILABLE"
        with pytest.raises(NotificationUnavailable):
            await transport.wait_after(event(1).run_id, 0, 0.1)
        reader.xread = AsyncMock(side_effect=TimeoutError)
        assert await transport.wait_after(event(1).run_id, 4, 0.01) == 4

    asyncio.run(scenario())


def test_redis_hint_cursor_never_advances_journal_or_sse_cursor():
    async def scenario():
        sub = subscription(poll_interval=0.01)
        sub.notifier = MagicMock(wait_after=AsyncMock(side_effect=[700, 700]))
        sub.read_page = AsyncMock(
            side_effect=[
                reading(),
                reading([event(1), event(2, "run.completed")], status="succeeded", last=2),
            ]
        )
        result = [frame async for frame in sub.events(reading())]
        assert len(result) == 2 and sub.cursor == 2
        assert [call.args[0] for call in sub.read_page.await_args_list] == [0, 0]
        assert [call.args[1] for call in sub.notifier.wait_after.await_args_list] == [0, 700]

    asyncio.run(scenario())


def test_close_attempts_both_pools_even_when_one_close_fails(monkeypatch):
    import minimew.execution.notifications as notifications

    async def scenario():
        closed = asyncio.Event()

        async def close_reader():
            await asyncio.sleep(0.005)
            closed.set()

        writer = MagicMock(aclose=AsyncMock(side_effect=RedisConnectionError("private")))
        reader = MagicMock(aclose=close_reader)
        monkeypatch.setattr(notifications, "Redis", MagicMock(side_effect=[writer, reader]))
        transport = RedisNotifications(Settings(db_password="offline-only"))
        with pytest.raises(NotificationUnavailable):
            await transport.aclose()
        assert closed.is_set()

    asyncio.run(scenario())


def test_redis_failure_waits_before_same_pg_query_instead_of_spinning():
    async def scenario():
        sub = subscription(poll_interval=0.025)
        sub.notifier = MagicMock(wait_after=AsyncMock(side_effect=NotificationUnavailable))
        sub.read_page = AsyncMock(
            return_value=reading([event(1, "run.completed")], status="succeeded", last=1)
        )
        started = time.monotonic()
        assert len([frame async for frame in sub.events(reading())]) == 1
        assert time.monotonic() - started >= 0.02
        sub.read_page.assert_awaited_once_with(0)

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["normal", "caller-error", "publisher-error"])
def test_notification_lifespan_closes_even_when_probe_or_task_fails(monkeypatch, ending):
    import minimew.execution.outbox as outbox

    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()
        transport = MagicMock(
            probe=AsyncMock(side_effect=NotificationUnavailable), aclose=AsyncMock()
        )
        monkeypatch.setattr(outbox, "RedisNotifications", MagicMock(return_value=transport))

        async def run(self):
            try:
                entered.set()
                if ending == "publisher-error":
                    raise RuntimeError("programming error")
                await asyncio.Event().wait()
            finally:
                closed.set()

        monkeypatch.setattr(outbox.OutboxPublisher, "run", run)
        before = asyncio.all_tasks()

        async def operate():
            async with notification_lifespan(MagicMock(), Settings(db_password="offline")):
                await entered.wait()
                if ending == "caller-error":
                    raise RuntimeError("caller failed")

        if ending == "normal":
            await operate()
        else:
            with pytest.raises(RuntimeError):
                await operate()
        assert closed.is_set() and not (asyncio.all_tasks() - before)
        transport.aclose.assert_awaited_once()

    asyncio.run(scenario())
