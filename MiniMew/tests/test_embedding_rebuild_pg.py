"""Real PostgreSQL catalog/worker/RAG with a controlled Bailian HTTP response."""

import asyncio
import os
from types import SimpleNamespace
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select
from test_documents_pg import account, client, database_call, upload  # noqa: F401
from test_indexing_pg import worker
from test_vector_store import FakeBackend

from minimew.documents.profiles import BAILIAN_HYBRID, LOCAL_HYBRID
from minimew.documents.retrieval import published_scope
from minimew.documents.worker import WorkerSettings
from minimew.embeddings.bailian import BASE_URL, BailianEmbedding
from minimew.models import EmbeddingRebuild, IndexJob, ModelCredential
from minimew.rag.pipeline import RagPipeline
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import SPACE_HASH
from minimew.vector_store.milvus import MilvusBackend

PATH = "/v1/embedding-profiles/selection"
KEY = "fixture-never-use-live"
pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PostgreSQL required"
)


class LayoutBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.layouts = set()

    def ensure_collection(self, *, layout="dense-cosine-v1"):
        self.layouts.add(layout)

    def upsert(self, rows, *, layout="dense-cosine-v1"):
        assert layout in self.layouts
        super().upsert(rows)


def remote(sessions, calls, *, failure_at=None):
    def respond(request):
        import json

        assert str(request.url) == BASE_URL + "/embeddings"
        assert request.headers["Authorization"] == "Bearer " + KEY
        body = json.loads(request.content)
        assert body["model"] == "text-embedding-v4"
        assert body["dimensions"] == 512
        calls.append(body["input"])
        if len(calls) == failure_at:
            return httpx2.Response(401, json={"error": {"message": KEY}})
        return httpx2.Response(
            200,
            json={
                "model": "text-embedding-v4",
                "data": [
                    {"index": i, "embedding": [0.0, 2.0] + [0.0] * 510}
                    for i in reversed(range(len(body["input"])))
                ],
            },
        )

    return BailianEmbedding(
        sessions, Settings(), transport_factory=lambda: httpx2.MockTransport(respond)
    )


def launch(browser, headers, source="bailian", key=KEY, **kwargs):
    return browser.post(
        PATH,
        headers=headers,
        json={"source": source, **({"api_key": key} if key else {})},
        **kwargs,
    )


def version_ids(scope):
    return {item.scope.identity.version_id for item in scope.targets}


def test_atomic_rebuild_switch_query_and_switch_back(client, tmp_path):  # noqa: F811
    headers, owner = account(client)
    other, _ = account(client)
    project = client.post("/v1/projects", headers=headers, json={"name": "独立项目"}).json()["id"]
    base = [
        upload(client, headers, key=f"doc-{i}", content=f"部署步骤{i}。".encode()).json()
        for i in range(2)
    ]
    old = {UUID(item["version"]["id"]) for item in base}
    assert launch(client, other, params={"project_id": project}).status_code == 404
    not_ready = launch(client, headers)
    assert (
        not_ready.status_code == 409
        and not_ready.json()["detail"]["code"] == "EMBEDDING_PENDING_UPLOAD"
    )

    async def scenario(sessions):
        calls, backend = [], LayoutBackend()
        bailian = remote(sessions, calls)
        indexer = worker(
            sessions, tmp_path, backend, bailian=bailian, settings=WorkerSettings(lease_seconds=2)
        )
        try:
            assert await indexer.run_once() and await indexer.run_once()
            old_scope = await published_scope(sessions, owner)
            accepted = await asyncio.to_thread(launch, client, headers)
            assert accepted.status_code == 202, accepted.text
            assert KEY not in accepted.text and "credential_id" not in accepted.text
            rebuild = accepted.json()["rebuild"]
            assert rebuild["total"] == 2 and rebuild["completed"] == 0
            duplicate = await asyncio.to_thread(launch, client, headers)
            assert duplicate.json()["rebuild"]["id"] == rebuild["id"]
            rejected = await asyncio.to_thread(upload, client, headers, key="during")
            assert rejected.json()["detail"]["code"] == "EMBEDDING_REBUILD_IN_PROGRESS"
            project_view = await asyncio.to_thread(
                client.get, PATH, headers=headers, params={"project_id": project}
            )
            assert project_view.json()["current_source"] == "local"
            assert await indexer.run_once()
            assert version_ids(await published_scope(sessions, owner)) == old
            # The already verified candidate must survive its original worker's lease.
            await asyncio.sleep(2.05)
            assert await indexer.run_once()
            switched = await published_scope(sessions, owner)
            assert len(switched.targets) == 2 and version_ids(switched).isdisjoint(old)
            assert all(item.encoding_space != SPACE_HASH for item in switched.targets)
            assert switched.embedding_credential_id
            async with sessions() as session:
                item = await session.get(ModelCredential, switched.embedding_credential_id)
                assert KEY not in item.encrypted_value
                jobs = list(
                    await session.scalars(
                        select(IndexJob).where(IndexJob.rebuild_id == UUID(rebuild["id"]))
                    )
                )
                assert all(job.stage == "ready" and job.status == "succeeded" for job in jobs)
            runtime = SimpleNamespace(
                index=CandidateIndex(backend),
                sync=asyncio.to_thread,
                bailian=bailian,
                encoder=SimpleNamespace(encode_query=lambda _: [1.0] + [0.0] * 511),
            )
            pipeline = RagPipeline(sessions, runtime)
            assert len((await pipeline.search("如何部署", switched, owner)).rows) == 2
            assert calls[-1] == ["如何部署"]
            assert len((await pipeline.search("旧请求", old_scope, owner)).rows) == 2
            assert calls[-1] == ["如何部署"]
            historical = await asyncio.to_thread(
                client.get,
                f"/v1/documents/{base[0]['document']['id']}/versions/{base[0]['version']['id']}",
                headers=headers,
            )
            assert historical.status_code == 200 and historical.json()["status"] == "superseded"
            fresh = await asyncio.to_thread(upload, client, headers, key="after")
            assert fresh.status_code == 202
            assert (
                fresh.json()["version"]["build_configuration"]["embedding"]["model_id"]
                == "text-embedding-v4"
            )
            assert await indexer.run_once()
            returned = await asyncio.to_thread(launch, client, headers, "local", None)
            assert returned.json()["rebuild"]["total"] == 3
            for _ in range(3):
                assert await indexer.run_once()
            local = await published_scope(sessions, owner)
            assert len(local.targets) == 3 and local.embedding_credential_id is None
            assert all(item.encoding_space == SPACE_HASH for item in local.targets)
        finally:
            indexer.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_failure_keeps_publication_can_retry_and_skip_withdrawal(client, tmp_path):  # noqa: F811
    headers, owner = account(client)
    originals = [upload(client, headers, key=f"doc-{i}").json() for i in range(2)]
    old = {UUID(item["version"]["id"]) for item in originals}

    async def scenario(sessions):
        indexer = worker(
            sessions, tmp_path, LayoutBackend(), bailian=remote(sessions, [], failure_at=2)
        )
        try:
            assert await indexer.run_once() and await indexer.run_once()
            first = await asyncio.to_thread(launch, client, headers)
            assert await indexer.run_once() and await indexer.run_once()
            assert version_ids(await published_scope(sessions, owner)) == old
            async with sessions() as session:
                batch = await session.get(EmbeddingRebuild, UUID(first.json()["rebuild"]["id"]))
                assert (
                    batch.status == "failed"
                    and batch.error_code == "EMBEDDING_PROVIDER_AUTH_FAILED"
                )
            retry = await asyncio.to_thread(launch, client, headers, "bailian", None)
            assert retry.json()["rebuild"]["id"] != first.json()["rebuild"]["id"]
            dropped = await asyncio.to_thread(
                client.delete, f"/v1/documents/{originals[1]['document']['id']}", headers=headers
            )
            assert dropped.status_code == 200
            assert await indexer.run_once()
            assert len((await published_scope(sessions, owner)).targets) == 1
            after = await asyncio.to_thread(client.get, PATH, headers=headers)
            assert after.json()["rebuild"]["status"] == "succeeded"
        finally:
            indexer.pool.shutdown()

    asyncio.run(database_call(scenario))


@pytest.mark.skipif(
    os.environ.get("MINIMEW_EMBEDDING_MILVUS_TESTS") != "1",
    reason="Requires local Milvus and creates only project-owned test collections",
)
def test_project_bailian_hybrid_uses_real_milvus(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        client.app.state,
        "settings",
        client.app.state.settings.model_copy(update={"document_index_layout": LOCAL_HYBRID}),
    )
    headers, owner = account(client)
    project = client.post("/v1/projects", headers=headers, json={"name": "真实集合验收"}).json()[
        "id"
    ]
    created = client.post("/v1/threads", headers=headers, json={"project_id": project})
    assert created.status_code == 201, created.text
    thread = created.json()["id"]
    doc = client.post(
        "/v1/documents",
        headers=headers,
        data={"idempotency_key": "real-milvus", "project_id": project},
        files={"file": ("guide.md", "# 网络\n启动前检查链路。".encode())},
    )
    assert doc.status_code == 202, doc.text
    namespace = "minimew_project_" + UUID(project).hex

    async def scenario(sessions):
        backend = await asyncio.to_thread(MilvusBackend.connect, VectorSettings())
        target = backend.for_namespace(namespace)
        bailian = remote(sessions, [])
        indexer = worker(sessions, tmp_path, backend, bailian=bailian)
        try:
            assert await indexer.run_once()
            assert (await published_scope(sessions, owner, thread_id=UUID(thread))).targets[
                0
            ].scope.index_layout == LOCAL_HYBRID
            accepted = await asyncio.to_thread(
                launch, client, headers, params={"project_id": project}
            )
            assert accepted.status_code == 202
            assert await indexer.run_once()
            scope = await published_scope(sessions, owner, thread_id=UUID(thread))
            assert scope.targets[0].scope.index_layout == BAILIAN_HYBRID
            assert target.collection(BAILIAN_HYBRID) != target.collection(LOCAL_HYBRID)
            runtime = SimpleNamespace(
                index=CandidateIndex(backend),
                sync=asyncio.to_thread,
                bailian=bailian,
                encoder=None,
                reranker=None,
            )
            from minimew.rag.protocol import RagPolicy

            result = await RagPipeline(sessions, runtime).search(
                "检查链路", scope, owner, RagPolicy(strategy="hybrid", rerank=False)
            )
            assert len(result.rows) == 1
            assert result.trace.stages["dense_count"] == result.trace.stages["bm25_count"] == 1
            assert (await published_scope(sessions, owner)).targets == ()
        finally:
            indexer.pool.shutdown()
            try:
                for layout in (LOCAL_HYBRID, BAILIAN_HYBRID):
                    name = target.collection(layout)
                    if await asyncio.to_thread(
                        target._rpc, target.client.has_collection, collection_name=name
                    ):
                        await asyncio.to_thread(
                            target._rpc, target.client.drop_collection, collection_name=name
                        )
                    assert not await asyncio.to_thread(
                        target._rpc, target.client.has_collection, collection_name=name
                    )
            finally:
                await asyncio.to_thread(backend.close)

    asyncio.run(database_call(scenario))
