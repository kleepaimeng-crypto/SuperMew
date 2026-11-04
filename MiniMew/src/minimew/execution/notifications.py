"""Redis 只唤醒 Journal 读取；客户端与连接池属于应用的事件循环。"""

import asyncio
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from minimew.generated.run_event_v1 import RunEvent
from minimew.settings import Settings


class NotificationUnavailable(Exception):
    def __init__(self, code="REDIS_UNAVAILABLE"):
        self.code = code
        super().__init__(code)


class EventNotifier(Protocol):
    async def wait_after(self, run_id: UUID, after: int, seconds: float) -> int: ...


# XADD 和 TTL 原子完成；迟到/重复的序号已被更新的提醒覆盖，不产生第二份事件。
# 不用 Lua tonumber 比较 bigint，避免 2**53 以上的精度损失。
PUBLISH = """
local added = redis.pcall('XADD', KEYS[1], 'MAXLEN', '~', ARGV[3],
                         ARGV[1] .. '-0', 'event', ARGV[2])
if type(added) == 'table' and added.err then
    if not string.find(added.err, 'equal or smaller', 1, true) then
        return redis.error_reply(added.err)
    end
    redis.call('EXPIRE', KEYS[1], ARGV[4])
    return 0
end
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""


class RedisNotifications:
    def __init__(self, settings: Settings):
        self.prefix = settings.redis_key_prefix
        self.maxlen, self.ttl = settings.redis_stream_maxlen, settings.redis_stream_ttl_seconds
        common = dict(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=1,
            retry=Retry(NoBackoff(), 0),
            protocol=2,
        )
        # XREAD 的阻塞连接不能耗尽发布连接池；两个池各自有明确上限。
        self.writer = Redis(**common, max_connections=4, socket_timeout=2)
        self.reader = Redis(
            **common, max_connections=32, socket_timeout=settings.event_poll_seconds + 1
        )

    def stream_key(self, run_id: UUID) -> str:
        return f"{self.prefix}:{run_id}"

    async def probe(self):
        try:
            async with asyncio.timeout(2):
                await self.writer.ping()
        except (RedisError, OSError, TimeoutError):
            raise NotificationUnavailable from None

    async def publish(self, event: RunEvent):
        try:
            async with asyncio.timeout(3):
                return bool(
                    await self.writer.eval(
                        PUBLISH,
                        1,
                        self.stream_key(event.run_id),
                        str(event.sequence),
                        event.model_dump_json(),
                        self.maxlen,
                        self.ttl,
                    )
                )
        except (RedisError, OSError, TimeoutError):
            raise NotificationUnavailable from None

    async def wait_after(self, run_id: UUID, after: int, seconds: float) -> int:
        try:
            async with asyncio.timeout(seconds):
                response = await self.reader.xread(
                    {self.stream_key(run_id): f"{after}-0"},
                    count=100,
                    block=max(1, int(seconds * 1000)),
                )
            # 只读 transport ID，不把 Redis 副本正文当成已提交事实。
            return max(
                (int(entry_id.split("-")[0]) for _, rows in response for entry_id, _ in rows),
                default=after,
            )
        except TimeoutError:
            return after
        except (RedisError, OSError, ValueError, TypeError):
            raise NotificationUnavailable from None

    async def aclose(self):
        async with asyncio.timeout(3):
            results = await asyncio.gather(
                self.writer.aclose(), self.reader.aclose(), return_exceptions=True
            )
            if any(isinstance(result, BaseException) for result in results):
                raise NotificationUnavailable
