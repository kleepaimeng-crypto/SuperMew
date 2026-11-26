"""Cloud-only (MINIMEW_LOCAL_MODELS_ENABLED=false) unit and real-PG tests."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx2
import pytest
from test_documents_pg import database_call
from test_embedding_rebuild_pg import LayoutBackend
from test_project_models_pg import template
from test_threads_pg import account
from test_threads_pg import client as api_client

from minimew.documents.profiles import CURRENT_CHUNKER, hybrid_preset
from minimew.documents.retrieval import RetrievalScope, published_scope
from minimew.documents.storage import SourceStore
from minimew.documents.worker import IndexWorker
from minimew.embeddings.bailian import BailianEmbedding
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.embeddings.tokenizer import LocalTokenizer
from minimew.knowledge_runtime import KnowledgeRuntime
from minimew.rag.pipeline import RagPipeline
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import BAILIAN_PROFILE, PROFILE


def test_bundled_tokenizer_loads_without_cache_or_weights(tmp_path):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    tokenizer = LocalTokenizer.load(PROFILE, settings)
    assert tokenizer.count("你好，MiniMew") >= 4
    offsets = tokenizer.offsets("你好，MiniMew")
    assert offsets and offsets[0][0] == 0 and offsets[-1][1] == len("你好，MiniMew")


def test_knowledge_runtime_skips_local_models_when_disabled(monkeypatch):
    fake_backend = Mock()
    monkeypatch.setattr(
        "minimew.knowledge_runtime.LocalEmbeddingAdapter.load_cpu",
        Mock(side_effect=AssertionError("must not load local embedding")),
    )
    monkeypatch.setattr(
        "minimew.knowledge_runtime.LocalReranker.load",
        Mock(side_effect=AssertionError("must not load local reranker")),
    )
    monkeypatch.setattr(
        "minimew.knowledge_runtime.MilvusBackend.connect", Mock(return_value=fake_backend)
    )

    async def scenario():
        runtime = KnowledgeRuntime(local_models_enabled=False)
        try:
            await runtime.start()
            assert runtime.encoder is None
            assert runtime.reranker is None
            assert runtime.rerank_error == "LOCAL_MODELS_DISABLED"
            fake_backend.probe.assert_called_once_with()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_rag_pipeline_fails_closed_on_local_profile_when_encoder_disabled():
    runtime = SimpleNamespace(encoder=None, reranker=None, bailian=None, index=Mock())
    scope = RetrievalScope(
        user_id=uuid4(),
        targets=(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    async def scenario():
        with pytest.raises(EmbeddingError, match="LOCAL_MODELS_DISABLED"):
            await RagPipeline(None, runtime).search("测试问题", scope, scope.user_id)

    asyncio.run(scenario())


def test_index_worker_advertises_only_bailian_fingerprints_when_encoder_none():
    worker = IndexWorker(
        sessions=None,
        store=None,
        tokenizer=Mock(),
        encoder=None,
        backend=Mock(),
        bailian=Mock(),
    )
    try:
        local_fp = (
            hybrid_preset(PROFILE.id)
            .model_copy(
                update={
                    "source_format": "markdown",
                    "parser": "markdown-txt-v2",
                    "chunker": CURRENT_CHUNKER,
                }
            )
            .fingerprint()
        )
        bailian_fp = (
            hybrid_preset(BAILIAN_PROFILE.id)
            .model_copy(
                update={
                    "source_format": "markdown",
                    "parser": "markdown-txt-v2",
                    "chunker": CURRENT_CHUNKER,
                }
            )
            .fingerprint()
        )
        assert local_fp not in worker.fingerprints
        assert bailian_fp in worker.fingerprints
    finally:
        worker.pool.shutdown()


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMEW_DOCUMENT_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("MINIMEW_LOCAL_MODELS_ENABLED", "false")
    yield from api_client.__wrapped__(monkeypatch)


@pytest.mark.skipif(not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required")
def test_cloud_only_mode_enforces_api_selections_and_personal_kb_flow(cloud_client, tmp_path):
    user_id, headers = account(cloud_client)

    emb_settings = cloud_client.get("/v1/embedding-profiles/selection", headers=headers)
    assert emb_settings.status_code == 200
    assert emb_settings.json()["local_models_enabled"] is False

    rerank_settings = cloud_client.get("/v1/rerank-settings", headers=headers)
    assert rerank_settings.status_code == 200
    assert rerank_settings.json()["local_models_enabled"] is False

    # Creating a project with local defaults is rejected.
    bad_project = cloud_client.post("/v1/projects", headers=headers, json={"name": "本地项目"})
    assert bad_project.status_code == 409
    assert bad_project.json()["detail"]["code"] == "LOCAL_MODELS_DISABLED"

    # Creating a project with cloud templates succeeds.
    emb, _ = template(cloud_client, headers, "embedding", key="cloud-emb-key")
    rerank, _ = template(cloud_client, headers, "rerank", key="cloud-rerank-key")
    ok_project = cloud_client.post(
        "/v1/projects",
        headers=headers,
        json={
            "name": "云端项目",
            "embedding_configuration_id": emb["id"],
            "rerank_configuration_id": rerank["id"],
        },
    )
    assert ok_project.status_code == 201, ok_project.text

    # Personal KB upload fails before configuring Bailian embedding.
    blocked_upload = cloud_client.post(
        "/v1/documents",
        headers=headers,
        data={"idempotency_key": "cloud-only-before-key"},
        files={"file": ("note.md", b"# Hello\ncloud only", "text/markdown")},
    )
    assert blocked_upload.status_code == 409
    assert blocked_upload.json()["detail"]["code"] == "LOCAL_MODELS_DISABLED"

    # Switching personal KB to local embedding or rerank is rejected.
    local_emb = cloud_client.post(
        "/v1/embedding-profiles/selection", headers=headers, json={"source": "local"}
    )
    assert local_emb.status_code == 409
    assert local_emb.json()["detail"]["code"] == "LOCAL_MODELS_DISABLED"

    local_rerank = cloud_client.put(
        "/v1/rerank-settings", headers=headers, json={"source": "local"}
    )
    assert local_rerank.status_code == 409
    assert local_rerank.json()["detail"]["code"] == "LOCAL_MODELS_DISABLED"

    # Configuring Bailian on an empty personal KB succeeds immediately and unblocks upload.
    switched = cloud_client.post(
        "/v1/embedding-profiles/selection",
        headers=headers,
        json={"source": "bailian", "api_key": "personal-bailian-key"},
    )
    assert switched.status_code == 202, switched.text
    body = switched.json()
    assert body["current_source"] == "bailian"
    assert body["rebuild"]["status"] == "succeeded"

    allowed_upload = cloud_client.post(
        "/v1/documents",
        headers=headers,
        data={"idempotency_key": "cloud-only-after-key"},
        files={"file": ("note.md", b"# Hello\ncloud only", "text/markdown")},
    )
    assert allowed_upload.status_code == 202, allowed_upload.text
    assert (
        allowed_upload.json()["version"]["build_configuration"]["embedding"]["id"]
        == "bailian-text-embedding-v4"
    )

    # Cloud-only IndexWorker (encoder=None) + bundled tokenizer builds and publishes the job.
    async def run_cloud_pipeline(sessions):
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

        backend = LayoutBackend()
        remote = BailianEmbedding(
            sessions, Settings(), transport_factory=lambda: httpx2.MockTransport(respond)
        )
        tokenizer = LocalTokenizer.load(PROFILE, EmbeddingRuntimeSettings(cache_dir=tmp_path))
        indexer = IndexWorker(
            sessions,
            SourceStore(tmp_path),
            tokenizer,
            None,
            backend,
            bailian=remote,
        )
        try:
            assert await indexer.run_once()
            scope = await published_scope(sessions, UUID(user_id))
            assert len(scope.targets) == 1 and scope.embedding_credential_id is not None
            runtime = SimpleNamespace(
                index=CandidateIndex(backend),
                sync=asyncio.to_thread,
                bailian=remote,
                encoder=None,
                reranker=None,
            )
            result = await RagPipeline(sessions, runtime).search("cloud", scope, UUID(user_id))
            assert len(result.rows) == 1
            assert calls == ["Bearer personal-bailian-key", "Bearer personal-bailian-key"]
        finally:
            indexer.pool.shutdown()

    asyncio.run(database_call(run_cloud_pipeline))
