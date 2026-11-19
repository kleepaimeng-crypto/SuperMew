import asyncio
import os
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from test_documents_pg import account, database_call
from test_indexing_pg import client as client

from minimew.account_models.credentials import CredentialResolver
from minimew.model_control import ModelConfigurationUnavailable
from minimew.models import ModelCredential, Run
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)
BASE = "/v1/account/model-settings"


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("MINIMEW_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode())


def create_run(client, headers, thread, key):
    return client.post(
        f"/v1/threads/{thread}/runs",
        headers=headers,
        json=dict(content="问题", idempotency_key=key),
    )


def test_encrypted_account_configuration_isolation_rotation_frozen_runs_and_revocation(
    client, monkeypatch
):
    headers, _ = account(client)
    thread = client.post("/v1/threads", headers=headers, json={}).json()["id"]
    payload = dict(
        name="我的DeepSeek",
        adapter="deepseek",
        model_name="deepseek-flash",
        api_key="private-unit-key-A",
    )
    created = client.post(BASE + "/models", headers=headers, json=payload)
    assert created.status_code == 201
    model = created.json()
    assert "private-unit-key" not in created.text and "credential_id" not in created.text
    selection = dict(model_id=model["id"], knowledge_enabled=False)
    assert client.put(BASE + "/selection", headers=headers, json=selection).status_code == 204
    first = create_run(client, headers, thread, "first")
    assert first.status_code == 201

    async def stored(sessions):
        async with sessions() as session:
            row = await session.get(Run, UUID(first.json()["id"]))
            assert not row.knowledge_enabled
            secret = await session.get(ModelCredential, row.credential_id)
            assert "private-unit-key" not in secret.encrypted_value
            assert "private-unit-key" not in str(row.model_snapshot_json)
            return row.credential_id, row.user_id, row.model_snapshot_json

    credential_a, owner, snapshot = asyncio.run(database_call(stored))
    # 配置切换和.env凭据缺失不妨碍账户模型，也不影响原幂等回执。
    monkeypatch.setenv("MINIMEW_DEEPSEEK_API_KEY", "")
    # 应用Settings已冻结，下面另有专门无部署Key的宿主测试。
    changed = client.put(
        BASE + f"/models/{model['id']}",
        headers=headers,
        json=payload | dict(api_key="private-unit-key-B", model_name="deepseek-v4-pro"),
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert create_run(client, headers, thread, "first").json() == first.json()
    second = create_run(client, headers, thread, "second")
    assert second.status_code == 201

    async def verify(sessions):
        async with sessions() as session:
            old = await session.get(Run, UUID(first.json()["id"]))
            new = await session.get(Run, UUID(second.json()["id"]))
            assert old.model_snapshot_json == snapshot and old.credential_id == credential_a
            assert (
                new.credential_id != credential_a
                and new.model_snapshot_json["answer"]["profile_version"] == 2
            )
        resolver = CredentialResolver(sessions, Settings())
        assert (
            await resolver(owner, credential_a, "https://api.deepseek.com")
        ).get_secret_value() == "private-unit-key-A"
        with pytest.raises(ModelConfigurationUnavailable):
            await resolver(
                UUID("11111111-1111-1111-1111-111111111111"),
                credential_a,
                "https://api.deepseek.com",
            )
        async with sessions() as session, session.begin():
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        update(Run).where(Run.id == old.id).values(credential_id=new.credential_id)
                    )

    asyncio.run(database_call(verify))
    others, _ = account(client)
    assert client.get(BASE, headers=others).json()["models"] == []
    assert client.put(BASE + "/selection", headers=others, json=selection).status_code == 404
    assert client.delete(BASE + f"/models/{model['id']}", headers=others).status_code == 404
    assert client.delete(BASE + f"/models/{model['id']}", headers=headers).status_code == 204
    assert client.get(BASE, headers=headers).json()["current_model_id"] is None

    async def revoked(sessions):
        with pytest.raises(ModelConfigurationUnavailable):
            await CredentialResolver(sessions, Settings())(
                owner, credential_a, "https://api.deepseek.com"
            )

    asyncio.run(database_call(revoked))


def test_default_uses_environment_and_invalid_key_storage_rolls_back(client, monkeypatch):
    headers, _ = account(client)
    state = client.get(BASE, headers=headers)
    assert state.status_code == 200 and state.headers["cache-control"] == "no-store"
    assert client.get(BASE).status_code == 401
    assert [p["id"] for p in client.get(BASE + "/presets", headers=headers).json()] == [
        "deepseek",
        "kimi",
        "qwen",
    ]
    bad = client.post(
        BASE + "/models",
        headers=headers,
        json=dict(
            name="Bad",
            adapter="generic",
            model_name="x",
            base_url="http://127.0.0.1",
            api_key="private-input",
        ),
    )
    assert bad.status_code == 422 and "private-input" not in bad.text
    thread = client.post("/v1/threads", headers=headers, json={}).json()["id"]
    first = create_run(client, headers, thread, "environment")
    assert first.status_code == 201

    async def verify(sessions):
        async with sessions() as session:
            row = await session.get(Run, UUID(first.json()["id"]))
            assert row.credential_id is None
            assert (
                row.model_snapshot_json["answer"]["configuration"]["model_name"]
                == Settings().default_model_name
            )

    asyncio.run(database_call(verify))
