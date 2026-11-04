"""真实本地 HTTP 流的测试工具；只管理自己绑定的临时 socket 和服务器。"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import uvicorn
from fastapi import FastAPI


@dataclass(frozen=True)
class SSEFrame:
    event: str | None
    data: dict | None
    comment: str | None = None


async def frames(response: httpx.Response) -> AsyncIterator[SSEFrame]:
    """按空行拆包，并严格检查本阶段没有 id 或其他旁路游标。"""
    fields: dict[str, str] = {}
    async for line in response.aiter_lines():
        if line.startswith(":"):
            assert not fields, "Heartbeat must not split an event frame"
            yield SSEFrame(None, None, line[1:].strip())
        elif not line:
            if fields:
                assert set(fields) == {"event", "data"}
                payload = json.loads(fields["data"])
                assert fields["event"] == payload["type"]
                yield SSEFrame(fields["event"], payload)
                fields = {}
        else:
            field, separator, value = line.partition(":")
            assert separator and field in {"event", "data"}, line
            assert field not in fields, "Canonical event uses one data line"
            fields[field] = value.removeprefix(" ")
    assert not fields, "Stream ended in the middle of an event frame"


async def next_event(source: AsyncIterator[SSEFrame]) -> dict:
    async with asyncio.timeout(5):
        async for frame in source:
            if frame.data is not None:
                return frame.data
    raise AssertionError("Stream closed before the expected event")


async def next_heartbeat(source: AsyncIterator[SSEFrame]) -> SSEFrame:
    async with asyncio.timeout(5):
        frame = await anext(source)
    assert frame.event is None and frame.data is None and frame.comment is not None
    return frame


async def remaining_events(source: AsyncIterator[SSEFrame]) -> list[dict]:
    async with asyncio.timeout(5):
        return [frame.data async for frame in source if frame.data is not None]


@dataclass(frozen=True)
class HTTPServer:
    app: FastAPI
    server: uvicorn.Server
    base_url: str
    loop: asyncio.AbstractEventLoop

    async def idle(self, *, disconnected: bool = False):
        """在服务器自己的 loop 上观察连接和 request task 的释放。"""

        async def wait_for_cleanup():
            async with asyncio.timeout(5):
                while True:
                    pool = self.app.state.session_factory.kw["bind"].pool
                    no_requests = not self.server.server_state.tasks
                    if pool.checkedout() == 0 and (no_requests or not disconnected):
                        return
                    await asyncio.sleep(0.005)

        result = asyncio.run_coroutine_threadsafe(wait_for_cleanup(), self.loop)
        await asyncio.wait_for(asyncio.wrap_future(result), 6)


@contextmanager
def serve_http(app: FastAPI):
    owned_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    owned_socket.bind(("127.0.0.1", 0))
    owned_socket.listen(128)
    port = owned_socket.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            log_level="error",
            access_log=False,
            ws="none",
            lifespan="on",
            timeout_graceful_shutdown=1,
        )
    )
    errors, loops = [], []

    async def run():
        loops.append(asyncio.get_running_loop())
        await server.serve(sockets=[owned_socket])

    def in_thread():
        try:
            asyncio.run(run())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=in_thread, name="minimew-test-http", daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 8
        startup_tick = threading.Event()
        while not server.started:
            assert worker.is_alive() and not errors, "Owned HTTP server failed to start"
            assert time.monotonic() < deadline, "Owned HTTP server startup timed out"
            startup_tick.wait(0.005)
        yield HTTPServer(app, server, f"http://127.0.0.1:{port}", loops[0])
    finally:
        server.should_exit = True
        worker.join(5)
        if worker.is_alive():
            server.force_exit = True
            worker.join(2)
        owned_socket.close()
        assert not worker.is_alive(), "Owned HTTP server leaked its thread"
        assert not errors, "Owned HTTP server failed while serving requests"
