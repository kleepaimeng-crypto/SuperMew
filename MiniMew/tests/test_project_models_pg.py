"""Real API/PG isolation, immutable project bindings and versioned credentials."""

import asyncio
import json
import os
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from test_documents_pg import database_call
from test_threads_pg import account
from test_threads_pg import client as api_client

from minimew.account_models.credentials import decrypt
from minimew.embeddings.bailian import BASE_URL, BailianEmbedding
from minimew.models import KnowledgeBase, ModelCredential, Project, Run
from minimew.rag.bailian_reranker import RERANK_URL, BailianReranker
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMEW_DOCUMENT_UPLOAD_DIR", str(tmp_path))
    yield from api_client.__wrapped__(monkeypatch)


def template(client, headers, purpose, key="fixture-old-key"):  # noqa: F811
    payload = dict(
        name=purpose,
        purpose=purpose,
        provider="bailian",
        api_key=key,
        model_name="text-embedding-v4" if purpose == "embedding" else "qwen3-rerank",
        base_url=BASE_URL if purpose == "embedding" else RERANK_URL,
    )
    response = client.post("/v1/retrieval-models", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    assert key not in response.text and "credential_id" not in response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json(), payload


def create(client, headers, **choices):  # noqa: F811
    result = client.post("/v1/projects", headers=headers, json={"name": "项目", **choices})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_reuse_edit_and_key_rotation_do_not_rebind_other_projects(client):  # noqa: F811
    _, owner = account(client)
    _, stranger = account(client)
    emb, emb_data = template(client, owner, "embedding")
    rerank, _ = template(client, owner, "rerank")
    choices = dict(embedding_configuration_id=emb["id"], rerank_configuration_id=rerank["id"])
    a, b = create(client, owner, **choices), create(client, owner, **choices)
    path = f"/v1/projects/{a}/models"
    before = client.get(path, headers=owner)
    assert before.status_code == 200 and before.json()["embedding"]["source"] == "bailian"
    assert client.get(path, headers=stranger).status_code == 404
    assert client.get("/v1/retrieval-models", headers=stranger).json() == []
    assert (
        client.put(
            path + "/embedding/credential", headers=stranger, json={"api_key": "forbidden"}
        ).status_code
        == 404
    )
    assert (
        client.put(
            path + "/embedding/credential",
            headers=owner,
            json={"api_key": "forbidden", "model_name": "different"},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/v1/rerank-settings", headers=owner, params={"project_id": a}, json={"source": "local"}
        ).json()["detail"]["code"]
        == "PROJECT_MODELS_IMMUTABLE"
    )
    assert (
        client.post(
            "/v1/embedding-profiles/selection",
            headers=owner,
            params={"project_id": a},
            json={"source": "local"},
        ).json()["detail"]["code"]
        == "PROJECT_MODELS_IMMUTABLE"
    )

    changed = {
        **emb_data,
        "api_key": "fixture-template-new",
        "base_url": BASE_URL.replace("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"),
    }
    assert (
        client.put(f"/v1/retrieval-models/{emb['id']}", headers=owner, json=changed).status_code
        == 200
    )
    c = create(client, owner, **choices)
    assert client.get(path, headers=owner).json() == before.json()
    assert (
        client.get(f"/v1/projects/{c}/models", headers=owner).json()["embedding"]["base_url"]
        == changed["base_url"]
    )
    assert (
        client.put(
            path + "/embedding/credential", headers=owner, json={"api_key": "fixture-project-new"}
        ).status_code
        == 200
    )

    async def check(sessions):
        async with sessions() as session:
            rows = [
                await session.scalar(
                    select(KnowledgeBase).where(KnowledgeBase.project_id == UUID(pid))
                )
                for pid in (a, b, c)
            ]
            assert len({row.embedding_credential_id for row in rows}) == 3
            assert rows[0].project_models == rows[1].project_models
            expected = ["fixture-project-new", "fixture-old-key", "fixture-template-new"]
            for kb, key in zip(rows, expected, strict=True):
                credential = await session.get(ModelCredential, kb.embedding_credential_id)
                assert key not in credential.encrypted_value
                assert (
                    decrypt(
                        Settings(), credential, kb.project_models["embedding"]["base_url"]
                    ).get_secret_value()
                    == key
                )
        for field, value in (("rerank_use_bailian", False), ("project_models", {})):
            async with sessions() as session:
                with pytest.raises(IntegrityError):
                    async with session.begin():
                        await session.execute(
                            update(KnowledgeBase)
                            .where(KnowledgeBase.project_id == UUID(a))
                            .values(**{field: value})
                        )
        # Both indexing/query Embedding and Rerank use the frozen endpoint and actual encrypted Key.
        calls = []

        def respond(request):
            calls.append((str(request.url), request.headers["Authorization"]))
            body = json.loads(request.content)
            if str(request.url).endswith("/embeddings"):
                return httpx2.Response(
                    200,
                    json={
                        "model": "text-embedding-v4",
                        "data": [
                            {"index": i, "embedding": [1.0] + [0.0] * 511}
                            for i in range(len(body["input"]))
                        ],
                    },
                )
            return httpx2.Response(
                200,
                json={"model": "qwen3-rerank", "results": [{"index": 0, "relevance_score": 0.8}]},
            )

        emb_adapter = BailianEmbedding(
            sessions, Settings(), transport_factory=lambda: httpx2.MockTransport(respond)
        )
        rr_adapter = BailianReranker(
            sessions, Settings(), transport_factory=lambda: httpx2.MockTransport(respond)
        )
        for kb in rows:
            assert (
                len(await emb_adapter.encode(kb.user_id, kb.embedding_credential_id, ["text"])) == 1
            )
        assert await rr_adapter.score(
            rows[0].user_id, rows[0].rerank_credential_id, "query", ["text"]
        ) == [0.8]
        assert calls == [
            (BASE_URL + "/embeddings", "Bearer fixture-project-new"),
            (BASE_URL + "/embeddings", "Bearer fixture-old-key"),
            (changed["base_url"] + "/embeddings", "Bearer fixture-template-new"),
            (RERANK_URL, "Bearer fixture-old-key"),
        ]

    asyncio.run(database_call(check))


def test_local_default_and_invalid_selection_are_atomic(client):  # noqa: F811
    user, owner = account(client)
    _, other = account(client)
    config, _ = template(client, other, "embedding")
    assert (
        client.post(
            "/v1/projects",
            headers=owner,
            json={"name": "越权", "embedding_configuration_id": config["id"]},
        ).status_code
        == 404
    )

    async def count(sessions):
        async with sessions() as session:
            return list(await session.scalars(select(Project).where(Project.user_id == UUID(user))))

    assert asyncio.run(database_call(count)) == []
    local = create(client, owner)
    selected = client.get(f"/v1/projects/{local}/models", headers=owner).json()
    assert selected["embedding"]["model_name"] == "BAAI/bge-small-zh-v1.5"
    assert selected["rerank"]["source"] == "local"
    assert (
        client.put(
            f"/v1/projects/{local}/models/rerank/credential",
            headers=owner,
            json={"api_key": "unused"},
        ).status_code
        == 409
    )
    wrong, _ = template(client, owner, "rerank")
    assert (
        client.post(
            "/v1/projects",
            headers=owner,
            json={"name": "用途错误", "embedding_configuration_id": wrong["id"]},
        ).status_code
        == 422
    )


def test_archive_keeps_run_and_history_and_restores_original_scope(client):  # noqa: F811
    _, owner = account(client)
    _, other = account(client)
    project = create(client, owner)
    for scope in (None, project):
        thread = client.post("/v1/threads", headers=owner, json={"project_id": scope}).json()
        tid = thread["id"]
        run = client.post(
            f"/v1/threads/{tid}/runs",
            headers=owner,
            json={"idempotency_key": "archive", "content": "保留历史"},
        )
        assert run.status_code == 201, run.text
        history = client.get(f"/v1/threads/{tid}/messages", headers=owner).json()
        params = {"project_id": scope} if scope else {}
        assert (
            client.patch(f"/v1/threads/{tid}", headers=other, json={"archived": True}).status_code
            == 404
        )
        for _ in range(2):
            assert (
                client.patch(f"/v1/threads/{tid}", headers=owner, json={"archived": True}).json()[
                    "archived"
                ]
                is True
            )
        assert tid not in [
            r["id"] for r in client.get("/v1/threads", headers=owner, params=params).json()["items"]
        ]
        assert tid in [
            r["id"]
            for r in client.get(
                "/v1/threads", headers=owner, params={**params, "archived": True}
            ).json()["items"]
        ]
        assert client.get(f"/v1/threads/{tid}/messages", headers=owner).json() == history

        async def saved(sessions):
            async with sessions() as session:
                return (await session.get(Run, UUID(run.json()["id"]))).status

        assert asyncio.run(database_call(saved)) == run.json()["status"]
        restored = client.patch(
            f"/v1/threads/{tid}", headers=owner, json={"archived": False}
        ).json()
        assert restored["project_id"] == scope and restored["archived"] is False
        assert (
            client.get("/v1/threads", headers=owner, params={**params, "archived": True}).json()[
                "items"
            ]
            == []
        )


def test_project_cloud_upload_worker_publish_and_query_use_bound_model(client, tmp_path):
    from types import SimpleNamespace

    from test_embedding_rebuild_pg import LayoutBackend
    from test_indexing_pg import worker

    from minimew.documents.retrieval import published_scope
    from minimew.rag.pipeline import RagPipeline
    from minimew.vector_store.adapter import CandidateIndex

    user, owner = account(client)
    config, _ = template(client, owner, "embedding")
    project = create(client, owner, embedding_configuration_id=config["id"])
    tid = client.post("/v1/threads", headers=owner, json={"project_id": project}).json()["id"]
    response = client.post(
        "/v1/documents",
        headers=owner,
        data={"project_id": project, "idempotency_key": "bound-upload"},
        files={"file": ("policy.md", b"# Policy\nA project has fixed models.")},
    )
    assert response.status_code == 202, response.text
    assert (
        response.json()["version"]["build_configuration"]["embedding"]["model_id"]
        == "text-embedding-v4"
    )
    assert (
        client.get(
            "/v1/embedding-profiles/selection", headers=owner, params={"project_id": project}
        ).json()["bailian_key_configured"]
        is True
    )

    class Backend(LayoutBackend):
        def for_namespace(self, name):
            self.namespace = name
            return self

    async def scenario(sessions):
        calls = []

        def respond(request):
            calls.append(request.headers["Authorization"])
            body = json.loads(request.content)
            assert body["model"] == "text-embedding-v4"
            return httpx2.Response(
                200,
                json={
                    "model": "text-embedding-v4",
                    "data": [
                        {"index": i, "embedding": [1.0] + [0.0] * 511}
                        for i in range(len(body["input"]))
                    ],
                },
            )

        backend = Backend()
        remote = BailianEmbedding(
            sessions, Settings(), transport_factory=lambda: httpx2.MockTransport(respond)
        )
        indexer = worker(sessions, tmp_path, backend, bailian=remote)
        try:
            assert await indexer.run_once()
            scope = await published_scope(sessions, UUID(user), thread_id=UUID(tid))
            assert len(scope.targets) == 1 and scope.embedding_credential_id
            assert not (await published_scope(sessions, UUID(user))).targets
            runtime = SimpleNamespace(
                index=CandidateIndex(backend),
                sync=asyncio.to_thread,
                bailian=remote,
                encoder=None,
                reranker=None,
            )
            assert (
                len(
                    (await RagPipeline(sessions, runtime).search("project", scope, UUID(user))).rows
                )
                == 1
            )
            assert calls == ["Bearer fixture-old-key", "Bearer fixture-old-key"]
        finally:
            indexer.pool.shutdown()

    asyncio.run(database_call(scenario))
