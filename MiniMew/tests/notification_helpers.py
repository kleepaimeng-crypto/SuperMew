"""真实 Redis 验收只管理自己创建的容器，不停止开发 Redis。"""

import asyncio
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from minimew.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


class OwnedRedis:
    def __init__(self):
        assert os.environ.get("MINIMEW_NOTIFICATION_TESTS") == "1"
        self.name = "minimew_test_redis_" + uuid4().hex
        self.password = secrets.token_urlsafe(24)
        self.created = False
        # Docker 的空 HostPort 在 stop/start 后可能重新分配，故为本次容器固定临时端口。
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]

    def docker(self, *args):
        return subprocess.run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(os.environ, MINIMEW_REDIS_PASSWORD=self.password),
        ).stdout.strip()

    def __enter__(self):
        image = next(
            line.strip().removeprefix("image: ")
            for line in (ROOT / "compose.yaml").read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("image: redis:")
        )
        script = ROOT / "docker" / "redis-entrypoint.sh"
        try:
            self.docker(
                "create",
                "--name",
                self.name,
                "-p",
                f"127.0.0.1:{self.port}:6379",
                "--env",
                "MINIMEW_REDIS_PASSWORD",
                "--mount",
                f"type=bind,source={script},target=/minimew-entrypoint.sh,readonly",
                "--entrypoint",
                "/bin/sh",
                image,
                "/minimew-entrypoint.sh",
            )
            self.created = True
            self.docker("start", self.name)
            binding = self.docker("port", self.name, "6379/tcp")
            assert binding.startswith("127.0.0.1:") and "\n" not in binding
            self.port = int(binding.rsplit(":", 1)[1])
            self.ready()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def ready(self):
        client = Redis(
            host="127.0.0.1",
            port=self.port,
            password=self.password,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            retry=Retry(NoBackoff(), 0),
        )
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    if client.ping():
                        return
                except RedisError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("Owned Redis did not become ready") from None
                    time.sleep(0.03)
        finally:
            client.close()

    async def stop(self):
        await asyncio.to_thread(self.docker, "stop", "--time", "1", self.name)

    async def start(self):
        await asyncio.to_thread(self.docker, "start", self.name)
        await asyncio.to_thread(self.ready)

    def settings(self):
        return Settings(
            redis_host="127.0.0.1",
            redis_port=self.port,
            redis_password=self.password,
            redis_key_prefix="minimew:test:" + uuid4().hex,
        )

    def __exit__(self, *args):
        if self.created:
            assert (
                self.name.startswith("minimew_test_redis_")
                and len(self.name.removeprefix("minimew_test_redis_")) == 32
            )
            self.docker("rm", "--force", "--volumes", self.name)
            self.created = False
