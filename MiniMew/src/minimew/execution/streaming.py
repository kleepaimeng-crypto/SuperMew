"""SSE 是 Journal 的有界只读投影；连接生命周期不拥有 Run 终态。"""

import asyncio
import logging
import math
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse, StreamingResponse

from minimew.conversations import ConversationNotFound
from minimew.execution.notifications import EventNotifier, NotificationUnavailable
from minimew.execution.queries import EventCursorAhead, EventRead, events_after
from minimew.generated.run_event_v1 import EVENT_ADAPTER, RunEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamOptions:
    poll_interval: float = 5.0
    heartbeat_interval: float = 15.0
    send_timeout: float = 10.0
    max_seconds: float = 300.0
    page_size: int = 100

    def __post_init__(self):
        for name in ("poll_interval", "heartbeat_interval", "send_timeout", "max_seconds"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 3600:
                raise ValueError("Stream intervals must be finite, positive and bounded")
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("Stream page size must be between 1 and 100")


class EventReadUnavailable(Exception):
    pass


def encode_event(event: RunEvent) -> bytes:
    # JSON 编码会转义正文中的换行，避免正文破坏 SSE 帧边界。
    data = EVENT_ADAPTER.dump_json(event).decode("utf-8")
    return f"event: {event.type}\ndata: {data}\n\n".encode("utf-8")


HEARTBEAT = b": keep-alive\n\n"


class EventSubscription:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        user_id: UUID,
        thread_id: UUID,
        run_id: UUID,
        *,
        after: int,
        expires_at: datetime,
        options: StreamOptions,
        notifier: EventNotifier | None = None,
    ):
        self.sessions = sessions
        self.user_id, self.thread_id, self.run_id = user_id, thread_id, run_id
        self.cursor = after
        self.expires_at = expires_at
        self.options = options
        self.notifier = notifier
        # 仅为防止重复提醒导致空查紧循环，不是业务发送/重连游标。
        self.notification_after = after

    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    async def read_page(self, after: int) -> EventRead:
        try:
            async with self.sessions() as session:
                return await events_after(
                    session,
                    self.user_id,
                    self.thread_id,
                    self.run_id,
                    after=after,
                    limit=self.options.page_size,
                )
        except (SQLAlchemyError, TimeoutError, OSError, ValidationError):
            raise EventReadUnavailable from None

    async def events(self, initial: EventRead) -> AsyncGenerator[bytes, None]:
        loop = asyncio.get_running_loop()
        last_sent = loop.time()
        result = initial
        while not self.expired():
            for event in result.page.items:
                if self.expired():
                    return
                yield encode_event(event)
                self.cursor = event.sequence
                last_sent = loop.time()
            if result.terminal and self.cursor >= result.last_sequence:
                return
            if not result.page.has_more:
                # 无数据库 Session/事务跨越发送或等待。
                remaining = (self.expires_at - datetime.now(UTC)).total_seconds()
                seconds = min(
                    self.options.poll_interval,
                    max(0, self.options.heartbeat_interval - (loop.time() - last_sent)),
                    max(0, remaining),
                )
                wait_until = loop.time() + seconds
                if self.notifier is None:
                    # 离线测试显式注入无通知资源，仍使用同一 Journal 查询路径。
                    await asyncio.sleep(seconds)
                else:
                    try:
                        self.notification_after = await self.notifier.wait_after(
                            self.run_id, max(self.cursor, self.notification_after), seconds
                        )
                    except NotificationUnavailable:
                        logger.warning(
                            "event_notification_wait_failed code=REDIS_UNAVAILABLE run_id=%s",
                            self.run_id,
                        )
                        await asyncio.sleep(max(0, wait_until - loop.time()))
                if self.expired():
                    return
                if loop.time() - last_sent >= self.options.heartbeat_interval:
                    yield HEARTBEAT
                    last_sent = loop.time()
            try:
                result = await self.read_page(self.cursor)
            except (EventReadUnavailable, ConversationNotFound, EventCursorAhead):
                logger.warning("event_stream_read_failed run_id=%s", self.run_id)
                return


class EventStreamResponse(StreamingResponse):
    """同一流响应内监控断连/有效期；发送背压不会积攒应用层事件队列。"""

    media_type = "text/event-stream"

    def __init__(self, subscription: EventSubscription, initial: EventRead):
        super().__init__(
            subscription.events(initial),
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self.subscription = subscription

    async def __call__(self, scope, receive, send):
        loop = asyncio.get_running_loop()
        start_attempted = started = finished = disconnected = cancelled = False
        deadline = loop.time() + self.subscription.options.max_seconds

        async def watch_limits():
            # Windows 的 loop 单调时钟可只有 15.625ms 分辨率；一次性换算 JWT 截止
            # 再依赖 timeout_at，可能比 UTC exp 提前结束。唤醒后复核真实有效期。
            resolution = time.get_clock_info("monotonic").resolution
            while True:
                remaining = min(
                    deadline - loop.time(),
                    (self.subscription.expires_at - datetime.now(UTC)).total_seconds(),
                )
                if remaining <= 0:
                    return
                await asyncio.sleep(max(resolution, min(remaining, 1.0)))

        async def bounded_send(message):
            nonlocal start_attempted, started, finished
            # timeout_at 对完全不挂起的 send 不保证及时中断，调用前也要明确检查。
            if loop.time() >= deadline or self.subscription.expired():
                raise TimeoutError
            if message["type"] == "http.response.start":
                start_attempted = True
            async with asyncio.timeout_at(
                min(deadline, loop.time() + self.subscription.options.send_timeout)
            ):
                await send(message)
                if message["type"] == "http.response.start":
                    started = True
                elif message["type"] == "http.response.body" and not message.get(
                    "more_body", False
                ):
                    finished = True

        stream_task = asyncio.create_task(self.stream_response(bounded_send))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(receive))
        limits_task = asyncio.create_task(watch_limits())
        try:
            async with asyncio.timeout_at(deadline):
                done, _ = await asyncio.wait(
                    {stream_task, disconnect_task, limits_task}, return_when=asyncio.FIRST_COMPLETED
                )
                disconnected = disconnect_task in done
                for task in done:
                    await task
        except (TimeoutError, OSError):
            # 此时响应可能已开始，不能重发 HTTP 状态或伪造业务结束事件。
            logger.info("event_stream_connection_closed run_id=%s", self.subscription.run_id)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            stream_task.cancel()
            disconnect_task.cancel()
            limits_task.cancel()
            await asyncio.gather(stream_task, disconnect_task, limits_task, return_exceptions=True)
            await self.body_iterator.aclose()
            if (
                not finished
                and not disconnected
                and not cancelled
                and (not start_attempted or started)
            ):
                # 到期/慢发送结束订阅，不发送新的业务数据；尽力完整结束 HTTP 正文。
                try:
                    async with asyncio.timeout(min(0.1, self.subscription.options.send_timeout)):
                        if not start_attempted:
                            expired = self.subscription.expired()
                            headers = {"Cache-Control": "no-store"}
                            if expired:
                                headers["WWW-Authenticate"] = "Bearer"
                            response = JSONResponse(
                                status_code=401 if expired else 503,
                                content={
                                    "detail": "Invalid credentials"
                                    if expired
                                    else {"code": "EVENT_STREAM_UNAVAILABLE"}
                                },
                                headers=headers,
                            )
                            await response(scope, receive, send)
                        else:
                            await send(
                                {"type": "http.response.body", "body": b"", "more_body": False}
                            )
                except (TimeoutError, OSError):
                    pass
