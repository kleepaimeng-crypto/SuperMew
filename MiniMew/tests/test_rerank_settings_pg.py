"""Real API, PostgreSQL, Run snapshot and credential resolution."""

import asyncio
import os
from uuid import UUID

import pytest
from sqlalchemy import select
from test_documents_pg import account, client, database_call, upload  # noqa: F401

from minimew.account_models.credentials import decrypt
from minimew.execution.repository import ExecutionRepository
from minimew.model_control import snapshot_for_run
from minimew.models import IndexJob, KnowledgeBase, ModelCredential, Run
from minimew.rag.bailian_reranker import RERANK_URL
from minimew.rag.protocol import BAILIAN_RERANKER, LOCAL_RERANKER
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PostgreSQL required"
)
SETTINGS = "/v1/rerank-settings"


def test_scope_and_frozen_binding_survive_switch_back(client):  # noqa: F811
    owner_headers, owner = account(client)
    other_headers, _ = account(client)

    def project(name):
        result = client.post("/v1/projects", headers=owner_headers, json={"name": name})
        assert result.status_code == 201
        return result.json()["id"]

    first, second = project("项目甲"), project("项目乙")
    params = {"project_id": first}
    doc = upload(client, owner_headers).json()
    assert client.get(SETTINGS, params=params, headers=other_headers).status_code == 404
    assert (
        client.put(
            SETTINGS, params=params, headers=owner_headers, json={"source": "bailian"}
        ).json()["detail"]["code"]
        == "RERANK_KEY_REQUIRED"
    )

    def select_model(source, key=None):
        result = client.put(
            SETTINGS,
            params=params,
            headers=owner_headers,
            json={"source": source, **({"api_key": key} if key else {})},
        )
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert "fixture-secret" not in result.text and "credential_id" not in result.text
        return result.json()

    def create_thread(project_id=None):
        result = client.post("/v1/threads", headers=owner_headers, json={"project_id": project_id})
        assert result.status_code == 201, result.text
        return result.json()["id"]

    def reserve(thread, key):
        return client.post(
            f"/v1/threads/{thread}/runs",
            headers=owner_headers,
            json={"idempotency_key": key, "content": "查询部署资料"},
        )

    assert select_model("bailian", "fixture-secret-old")["model"] == BAILIAN_RERANKER
    thread_a, thread_b, thread_personal = (
        create_thread(first),
        create_thread(second),
        create_thread(),
    )
    run_a = reserve(thread_a, "alpha")
    run_b = reserve(thread_b, "beta")
    run_personal = reserve(thread_personal, "personal")
    for response in (run_a, run_b, run_personal):
        assert response.status_code == 201, response.text
    run_ids = [UUID(r.json()["id"]) for r in (run_a, run_b, run_personal)]
    assert select_model("bailian", "fixture-secret-new")["model"] == BAILIAN_RERANKER
    assert select_model("local")["model"] == LOCAL_RERANKER
    assert reserve(thread_a, "alpha").json()["id"] == str(run_ids[0])
    after = reserve(thread_a, "local-change")
    assert after.status_code == 201, after.text
    assert "fixture-secret" not in run_a.text and "rerank_credential_id" not in run_a.text
    assert client.get(SETTINGS, headers=owner_headers).json()["source"] == "local"
    assert (
        client.get(SETTINGS, headers=owner_headers, params={"project_id": second}).json()["source"]
        == "local"
    )

    async def scenario(sessions):
        async with sessions() as session:
            rows = [await session.get(Run, identity) for identity in run_ids]
            cloud, project_local, personal_local = rows
            assert snapshot_for_run(cloud).rag.reranker == BAILIAN_RERANKER
            assert cloud.rerank_credential_id is not None and cloud.credential_id is None
            assert all(row.rerank_credential_id is None for row in (project_local, personal_local))
            assert all(
                snapshot_for_run(row).rag.reranker == LOCAL_RERANKER
                for row in (
                    project_local,
                    personal_local,
                    await session.get(Run, UUID(after.json()["id"])),
                )
            )
            kb = await session.scalar(
                select(KnowledgeBase).where(KnowledgeBase.project_id == UUID(first))
            )
            assert not kb.rerank_use_bailian and kb.embedding_credential_id is None
            assert kb.rerank_credential_id != cloud.rerank_credential_id
            credential = await session.get(ModelCredential, cloud.rerank_credential_id)
            assert "fixture-secret" not in credential.encrypted_value
            assert (
                decrypt(Settings(), credential, RERANK_URL).get_secret_value()
                == "fixture-secret-old"
            )
            assert len(list(await session.scalars(select(IndexJob)))) == 1
            assert (await session.get(IndexJob, UUID(doc["job"]["id"]))).status == "pending"

        repository = ExecutionRepository(sessions)
        claim = await repository.claim(owner, UUID(thread_a), run_ids[0])
        assert claim is not None
        request = await repository.request(claim)
        assert request.snapshot.rag.reranker == BAILIAN_RERANKER
        assert request.rerank_credential_id == cloud.rerank_credential_id

    asyncio.run(database_call(scenario))

    assert select_model("bailian")["bailian_key_configured"]
