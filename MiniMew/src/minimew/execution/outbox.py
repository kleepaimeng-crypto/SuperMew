"""短事务读取待办 → 外部发送 → 短事务确认；通知代码不拥有 Run。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from minimew.execution.journal import event_json
from minimew.execution.notifications import NotificationUnavailable, RedisNotifications
from minimew.generated.run_event_v1 import EVENT_ADAPTER
from minimew.models import EventOutbox, RunEventRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingNotification:
    event_id: UUID
    payload: str
    attempts: int


def retry_delay(attempts: int) -> float:
    return min(30, 2 ** min(attempts, 5))


class OutboxPublisher:
    def __init__(self, sessions, transport, *, scan_seconds=0.5, batch_size=20):
        self.sessions, self.transport = sessions, transport
        self.scan_seconds, self.batch_size = scan_seconds, batch_size

    async def read_due(self) -> list[PendingNotification]:
        async with self.sessions() as session, session.begin():
            rows = (
                await session.execute(
                    select(EventOutbox, RunEventRecord)
                    .join(RunEventRecord, RunEventRecord.event_id == EventOutbox.event_id)
                    .where(
                        EventOutbox.published_at.is_(None),
                        EventOutbox.next_attempt_at <= func.clock_timestamp(),
                    )
                    .order_by(EventOutbox.created_at, RunEventRecord.sequence, EventOutbox.event_id)
                    .limit(self.batch_size)
                )
            ).all()
            return [
                PendingNotification(outbox.event_id, event_json(event), outbox.attempts)
                for outbox, event in rows
            ]

    async def record_result(self, item: PendingNotification, code: str | None):
        values = dict(
            attempts=case(
                (EventOutbox.attempts < 1000000000, EventOutbox.attempts + 1),
                else_=EventOutbox.attempts,
            ),
            last_error_code=code,
        )
        if code is None:
            values["published_at"] = func.clock_timestamp()
        else:
            values["next_attempt_at"] = func.clock_timestamp() + timedelta(
                seconds=retry_delay(item.attempts)
            )
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(EventOutbox)
                .where(EventOutbox.event_id == item.event_id, EventOutbox.published_at.is_(None))
                .values(**values)
            )

    async def publish_once(self) -> int:
        items = await self.read_due()
        for item in items:
            try:
                event = EVENT_ADAPTER.validate_json(item.payload)
            except ValidationError:
                await self.record_result(item, "EVENT_INVALID")
                raise NotificationUnavailable("EVENT_INVALID") from None
            try:
                await self.transport.publish(event)
            except NotificationUnavailable as error:
                await self.record_result(item, error.code)
                raise
            await self.record_result(item, None)
        return len(items)

    async def run(self):
        failures = 0
        while True:
            try:
                async with asyncio.timeout(10):
                    count = await self.publish_once()
            except (NotificationUnavailable, SQLAlchemyError, TimeoutError, OSError) as error:
                code = (
                    error.code
                    if isinstance(error, NotificationUnavailable)
                    else "OUTBOX_STORAGE_UNAVAILABLE"
                )
                logger.warning("notification_publish_retry code=%s", code)
                delay = retry_delay(failures)
                failures = min(failures + 1, 5)
            else:
                failures = 0
                delay = self.scan_seconds if count < self.batch_size else 0
            await asyncio.sleep(delay)


@asynccontextmanager
async def notification_lifespan(sessions, settings):
    transport = RedisNotifications(settings)
    task = None
    try:
        try:
            await transport.probe()
        except NotificationUnavailable:
            logger.warning("notification_probe_failed code=REDIS_UNAVAILABLE")
        publisher = OutboxPublisher(
            sessions, transport, scan_seconds=settings.notification_scan_seconds
        )
        task = asyncio.create_task(publisher.run(), name="minimew-outbox-publisher")

        def report_failure(done):
            if not done.cancelled() and done.exception() is not None:
                logger.error("notification_publisher_stopped code=UNEXPECTED_PUBLISHER_ERROR")

        task.add_done_callback(report_failure)
        yield transport
    finally:
        try:
            if task is not None:
                task.cancel()
                try:
                    async with asyncio.timeout(3):
                        await task
                except asyncio.CancelledError:
                    if not task.cancelled():
                        raise
                except TimeoutError:
                    logger.warning("notification_shutdown_timeout")
        finally:
            try:
                await transport.aclose()
            except (NotificationUnavailable, RedisError, OSError, TimeoutError):
                logger.warning("notification_close_failed")
