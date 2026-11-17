"""工作台真实PG/鉴权/比较；模型与向量显式受控，无收费。"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_documents_pg import account, database_call
from test_documents_pg import client as client
from test_evaluation_runtime_pg import TestRuntime, seeded
from test_evaluation_runtime_pg import owned_evaluation_catalog as owned_evaluation_catalog

from minimew.evaluation.store import EvaluationStore
from minimew.evaluation.worker import EvaluationWorker
from minimew.models import EvaluationCorpus, EvaluationJob

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)
ROOT = "/v1/evaluation-jobs"


def setup(client, *, completed=False):
    headers, user = account(client)

    async def seed(sessions):
        request, backend = await seeded(sessions, user, True)
        store = EvaluationStore(sessions)
        job_id = await store.create(user, "workbench", request)
        if completed:
            worker = EvaluationWorker(
                sessions,
                runtime_factory=lambda: TestRuntime(backend),
                backend_factory=lambda *a, **k: backend,
            )
            assert await worker.once()
            job, cases = await store.read(user, job_id)
            assert job.status == "completed", (job.error_code, [c.error_code for c in cases])
        return request, job_id

    request, job_id = asyncio.run(database_call(seed))
    return headers, user, request, str(job_id)


def test_validation_checks_same_corpus_without_creating_job(client):
    headers, user, request, _ = setup(client)

    async def count(sessions):
        async with sessions() as session:
            return await session.scalar(select(func.count()).select_from(EvaluationJob))

    before = asyncio.run(database_call(count))
    response = client.post(
        ROOT + "/validate", headers=headers, json=request.model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "valid",
        "case_count": 1,
        "total_model_limit": 112,
        "mode": "controlled",
    }
    assert asyncio.run(database_call(count)) == before
    stranger, _ = account(client)
    assert (
        client.post(
            ROOT + "/validate", headers=stranger, json=request.model_dump(mode="json")
        ).status_code
        == 404
    )
    invalid = request.model_dump(mode="json")
    invalid["total_model_limit"] = "10"
    assert client.post(ROOT + "/validate", headers=headers, json=invalid).status_code == 422
    missing = request.model_dump(mode="json")
    missing["corpus_id"] = str(uuid4())
    assert client.post(ROOT + "/validate", headers=headers, json=missing).status_code == 404


def test_corpus_directory_filters_owner_readiness_expiry_and_paginates(client):
    headers, user, request, _ = setup(client)
    stranger, _ = account(client)

    async def extra(sessions):
        async with sessions() as session, session.begin():
            original = await session.get(EvaluationCorpus, request.corpus_id)
            for kind in ("ready", "expired", "building"):
                identifier = uuid4()
                payload = json.loads(json.dumps(original.payload))
                if kind == "expired":
                    payload["scope"]["expires_at"] = (
                        datetime.now(UTC) - timedelta(seconds=1)
                    ).isoformat()
                if kind == "building":
                    payload["ready"] = False
                session.add(
                    EvaluationCorpus(
                        id=identifier,
                        user_id=user,
                        collection="minimew_eval_" + identifier.hex,
                        payload=payload,
                    )
                )

    asyncio.run(database_call(extra))
    assert client.get(ROOT + "/corpora", headers=stranger).json()["items"] == []
    first = client.get(ROOT + "/corpora?limit=1", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["next_offset"] == 1
    second = client.get(ROOT + "/corpora?offset=1&limit=1", headers=headers)
    assert second.json()["next_offset"] is None
    assert first.json()["items"][0]["corpus_id"] != second.json()["items"][0]["corpus_id"]
    assert "text" not in first.json()["items"][0]
    assert client.get(ROOT + "/corpora?offset=-1", headers=headers).status_code == 422


def test_history_and_case_projection_hide_private_state(client):
    headers, user, request, job_id = setup(client, completed=True)

    async def more(sessions):
        return await EvaluationStore(sessions).create(user, "another", request)

    asyncio.run(database_call(more))
    page = client.get(ROOT + "?limit=1", headers=headers)
    assert page.status_code == 200 and page.json()["next_offset"] == 1
    assert (
        client.get(ROOT + "?offset=1&limit=1", headers=headers).json()["items"][0]["job_id"]
        == job_id
    )
    stranger, _ = account(client)
    assert client.get(ROOT, headers=stranger).json()["items"] == []
    path = f"{ROOT}/{job_id}/cases/meal"
    detail = client.get(path, headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["resumed"] and body["accepted"] and body["requested"]
    assert body["counts"]["model_attempts"] == 3 and body["judge"]["correctness"] == 2
    assert body["materials"] and body["answer"]
    assert all(
        secret not in detail.text
        for secret in ("checkpoint", "graph", "gold-sentinel", "required_claims", "owner_token")
    )
    assert client.get(path, headers=stranger).status_code == 404
    assert client.get(f"{ROOT}/{job_id}/cases/missing", headers=headers).status_code == 404


def test_baseline_comparison_is_owned_authoritative_and_explicit(client):
    headers, user, request, job_id = setup(client, completed=True)
    path = f"{ROOT}/{job_id}/compare"
    same = client.post(path, headers=headers, json={"baseline_id": job_id})
    assert same.status_code == 200, same.text
    assert same.json()["decision"] == "pass"
    assert same.json()["baseline_fingerprint"]
    assert all(d["change"] == 0 for d in same.json()["judge_deltas"])
    stranger, other = account(client)

    async def pending(sessions):
        store = EvaluationStore(sessions)
        other_request, _ = await seeded(sessions, other)
        other_job = await store.create(other, "other", other_request)
        own_pending = await store.create(user, "pending", request)
        return str(other_job), str(own_pending)

    other_job, pending_job = asyncio.run(database_call(pending))
    assert client.post(path, headers=headers, json={"baseline_id": other_job}).status_code == 404
    assert client.post(path, headers=stranger, json={"baseline_id": job_id}).status_code == 404
    assert client.post(path, headers=headers, json={"baseline_id": pending_job}).status_code == 409
    assert (
        client.post(
            path,
            headers=headers,
            json={"baseline_id": job_id, "policy": {"allowed_modes": ["live"]}},
        ).json()["decision"]
        == "fail"
    )
    assert client.post(path, headers=headers, json={"baseline_id": "bad"}).status_code == 422
