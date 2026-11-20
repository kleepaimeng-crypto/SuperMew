"""显式在线验收 POST → DeepSeek → Journal/Outbox → SSE；仅写自己创建的临时库。"""

import argparse
import asyncio
import os
import sys
from uuid import uuid4

import httpx
from check_runtime import ROOT, temporary_database
from fixture_models import model_environment

from minimew.app import create_app
from minimew.model_control import ModelConfiguration
from minimew.settings import Settings

sys.path.insert(0, str(ROOT / "tests"))
from notification_helpers import OwnedRedis  # noqa: E402
from sse_helpers import frames, serve_http  # noqa: E402
from test_runs_pg import account, submit  # noqa: E402


async def main():
    settings = Settings()
    if not settings.model_credentials_available:
        raise SystemExit("Configure MINIMEW_DEEPSEEK_API_KEY in the local .env first")
    async with temporary_database() as database:
        configuration = ModelConfiguration.model_validate_json(
            (ROOT / "model-profile.deepseek-v4-flash.json").read_text(encoding="utf-8")
        ).model_copy(update={"max_output_tokens": 128, "timeout_seconds": 60.0})
        updates = {
            **model_environment(configuration, adapter="deepseek"),
            "MINIMEW_DB_NAME": database,
            "MINIMEW_NOTIFICATION_TESTS": "1",
            "MINIMEW_JWT_SECRET": "online-smoke-only-" + uuid4().hex,
            "MINIMEW_EXECUTION_SCAN_SECONDS": "0.05",
            "MINIMEW_NOTIFICATION_SCAN_SECONDS": "0.02",
        }
        previous = {
            key: os.environ.get(key)
            for key in (
                *updates,
                "MINIMEW_REDIS_PORT",
                "MINIMEW_REDIS_PASSWORD",
                "MINIMEW_REDIS_KEY_PREFIX",
            )
        }
        try:
            os.environ.update(updates)
            with OwnedRedis() as redis:
                os.environ.update(
                    {
                        "MINIMEW_REDIS_PORT": str(redis.port),
                        "MINIMEW_REDIS_PASSWORD": redis.password,
                        "MINIMEW_REDIS_KEY_PREFIX": redis.settings().redis_key_prefix,
                    }
                )
                with serve_http(create_app()) as server:
                    with httpx.Client(base_url=server.base_url, timeout=8) as browser:
                        headers, thread = account(browser)
                        created = submit(
                            browser, headers, thread, content="仅回答 OK，不要添加其他文字。"
                        )
                        assert created.status_code == 201, "Run reservation failed"
                        run = created.json()["id"]
                    async with httpx.AsyncClient(base_url=server.base_url, timeout=75) as browser:
                        path = f"/v1/threads/{thread}/runs/{run}/events/stream"
                        async with (
                            asyncio.timeout(75),
                            browser.stream(
                                "GET",
                                path,
                                headers=headers,
                            ) as response,
                        ):
                            assert response.status_code == 200, "SSE failed"
                            events = [f.data async for f in frames(response) if f.data is not None]
                        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
                        if events[-1]["type"] != "run.completed":
                            code = events[-1]["data"].get("error_code", "INCOMPLETE_STREAM")
                            raise SystemExit(f"Online DeepSeek check failed code={code}")
                        assert events[-2]["type"] == "message.completed"
                        answer = events[-2]["data"]["content"]
                        assert answer.strip(), "Empty answer"
                        assert (
                            "".join(
                                e["data"]["content"] for e in events if e["type"] == "message.delta"
                            )
                            == answer
                        )
                        after = events[-2]["sequence"]
                        replay = await browser.get(
                            path.removesuffix("/stream"), params={"after": after}, headers=headers
                        )
                        assert [e["sequence"] for e in replay.json()["items"]] == [after + 1]
                        print(f"PASS: online DeepSeek, automatic Run, {len(events)} events, replay")
                        print(f"Public answer: {answer[:80]}")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--online", action="store_true", help="Explicitly allow a small paid model call"
    )
    if not parser.parse_args().online:
        parser.error("Pass --online to explicitly run the real DeepSeek check")
    asyncio.run(main())
