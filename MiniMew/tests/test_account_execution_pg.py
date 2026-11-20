import asyncio
import json
import os
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_deepseek import chunk, wire
from test_deepseek import transport as transport
from test_documents_pg import account, database_call
from test_indexing_pg import client as client

from minimew.account_models.credentials import CredentialResolver
from minimew.app import create_app
from minimew.execution.chat_provider import ChatProvider
from minimew.execution.repository import ExecutionRepository
from minimew.execution.service import RunExecutor
from minimew.models import Run, RunEventRecord
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)
BASE = "/v1/account/model-settings"


def test_account_key_executes_without_server_key_and_revoked_key_never_falls_back(
    client,
    monkeypatch,
    transport,
):
    headers, owner = account(client)
    item = client.post(
        BASE + "/models",
        headers=headers,
        json=dict(
            name="账户模型",
            adapter="deepseek",
            model_name="deepseek-flash",
            api_key="private-account-runtime-key",
        ),
    ).json()
    assert (
        client.put(
            BASE + "/selection",
            headers=headers,
            json=dict(model_id=item["id"], knowledge_enabled=False),
        ).status_code
        == 204
    )
    monkeypatch.setenv("MINIMEW_DEEPSEEK_API_KEY", "")

    async def response(req):
        assert req.headers["authorization"] == "Bearer private-account-runtime-key"
        body = json.loads(req.content)
        assert body["model"] == "deepseek-flash" and "tools" not in body
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk("账户密钥执行成功"), chunk(finish="stop")),
        )

    requests, _ = transport(response)
    with TestClient(create_app()) as fresh:
        thread = fresh.post("/v1/threads", headers=headers, json={}).json()["id"]

        def submit(key):
            return fresh.post(
                f"/v1/threads/{thread}/runs",
                headers=headers,
                json=dict(content="问题", idempotency_key=key),
            )

        first = submit("account-only")
        assert first.status_code == 201

        async def execute(sessions, rid):
            provider = ChatProvider(
                SecretStr(""), credentials=CredentialResolver(sessions, Settings())
            )
            result = await RunExecutor(ExecutionRepository(sessions), provider).execute(
                owner, UUID(thread), UUID(rid)
            )
            async with sessions() as session:
                row = await session.get(Run, UUID(rid))
                assert "private-account-runtime-key" not in str(row.model_snapshot_json)
                events = (
                    await session.scalars(
                        select(RunEventRecord).where(RunEventRecord.run_id == row.id)
                    )
                ).all()
                assert "private-account-runtime-key" not in str([e.data for e in events])
            return result

        assert asyncio.run(database_call(lambda s: execute(s, first.json()["id"]))) == "succeeded"
        # 新任务已冻结旧凭据，删除后按原Run失败，不重新取当前/默认Key。
        second = submit("revoked-before-execution")
        assert second.status_code == 201
        assert fresh.delete(BASE + f"/models/{item['id']}", headers=headers).status_code == 204
        assert asyncio.run(database_call(lambda s: execute(s, second.json()["id"]))) == "failed"
        assert len(requests) == 1
        replay = submit("account-only")
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]
        assert replay.json()["status"] == "succeeded"
        assert (
            replay.json()
            == fresh.get(f"/v1/threads/{thread}/runs/{first.json()['id']}", headers=headers).json()
        )
        assert submit("new-default-missing").status_code == 503


def test_missing_master_key_rejects_storage_without_leaking_input_or_partial_rows(client):
    from sqlalchemy import func

    from minimew.models import AccountModelConfiguration, ModelCredential

    headers, owner = account(client)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"credential_master_key": SecretStr("")}
    )
    failed = client.post(
        BASE + "/models",
        headers=headers,
        json=dict(
            name="未启用存储",
            adapter="deepseek",
            model_name="deepseek-flash",
            api_key="private-do-not-return",
        ),
    )
    assert failed.status_code == 503 and "private-do-not-return" not in failed.text

    async def rows(sessions):
        async with sessions() as session:
            for model in (AccountModelConfiguration, ModelCredential):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.user_id == owner)
                    )
                    == 0
                )

    asyncio.run(database_call(rows))


def test_api_dispatcher_uses_account_credentials_when_default_key_is_absent(
    client, monkeypatch, transport
):
    import time

    from minimew.execution.dispatcher import execution_lifespan

    headers, _ = account(client)
    item = client.post(
        BASE + "/models",
        headers=headers,
        json=dict(
            name="自动执行账户模型",
            adapter="deepseek",
            model_name="deepseek-flash",
            api_key="private-auto-account-key",
        ),
    ).json()
    assert (
        client.put(
            BASE + "/selection",
            headers=headers,
            json=dict(model_id=item["id"], knowledge_enabled=False),
        ).status_code
        == 204
    )
    monkeypatch.setenv("MINIMEW_DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("MINIMEW_EXECUTION_SCAN_SECONDS", "0.05")
    monkeypatch.setattr("minimew.app.execution_lifespan", execution_lifespan)

    async def response(req):
        assert req.headers["authorization"] == "Bearer private-auto-account-key"
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk("宿主自动执行成功"), chunk(finish="stop")),
        )

    requests, _ = transport(response)
    with TestClient(create_app()) as browser:
        thread = browser.post("/v1/threads", headers=headers, json={}).json()["id"]
        path = f"/v1/threads/{thread}/runs"
        created = browser.post(
            path, headers=headers, json=dict(content="宿主链验证", idempotency_key="auto-account")
        )
        assert created.status_code == 201
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = browser.get(path + "/" + created.json()["id"], headers=headers).json()
            if current["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        assert current["status"] == "succeeded"
        messages = browser.get(f"/v1/threads/{thread}/messages", headers=headers).json()["items"]
        assert messages[-1]["content"] == "宿主自动执行成功"
        assert "private-auto-account-key" not in str(messages)
        assert len(requests) == 1
