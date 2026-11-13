import asyncio
import os
from uuid import UUID

import pytest
from test_documents_pg import account, database_call, upload
from test_hybrid import HybridBackend
from test_indexing_pg import client as client
from test_indexing_pg import worker

from minimew.documents.retrieval import published_scope
from minimew.documents.storage import SourceStore
from minimew.documents.upgrade import upgrade
from minimew.models import Document, DocumentVersion, KnowledgeBase
from minimew.vector_store.manifest import HYBRID_LAYOUT

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PG required"
)


def test_upgrade_keeps_old_scope_source_and_versions_then_publishes_new_build(client, tmp_path):
    headers, user = account(client)
    value = upload(client, headers, content="# 制度\n餐费150元。".encode()).json()

    async def scenario(sessions):
        backend = HybridBackend()
        w = worker(sessions, tmp_path, backend=backend)
        try:
            assert await w.run_once()
            old = await published_scope(sessions, user)
            plan = await upgrade(sessions, SourceStore(tmp_path), user)
            assert plan["documents"] == 1 and not plan["jobs"]
            applied = await upgrade(sessions, SourceStore(tmp_path), user, apply=True)
            assert len(applied["jobs"]) == 1
            assert (await upgrade(sessions, SourceStore(tmp_path), user, apply=True))["jobs"] == []
            assert (await published_scope(sessions, user)).targets == old.targets
            assert await w.run_once()
            current = await published_scope(sessions, user)
            assert current.targets[0].scope.index_layout == HYBRID_LAYOUT
            assert (
                current.targets[0].scope.identity.version_id
                != old.targets[0].scope.identity.version_id
            )
            assert w._index is not None
            from minimew.vector_store.adapter import CandidateIndex

            assert CandidateIndex(backend).verify(old.targets[0])
            async with sessions() as s:
                prior = await s.get(DocumentVersion, UUID(value["version"]["id"]))
                assert prior.build_configuration["index_layout"] == "dense-cosine-v1"
                assert SourceStore(tmp_path).read(prior.id, prior.content_sha256)
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_rebuild_precondition_does_not_supersede_concurrent_upload(client, tmp_path):
    headers, user = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))
    new = upload(
        client, headers, document=value["document"]["id"], key="new", content=b"changed"
    ).json()

    async def check(sessions):
        with pytest.raises(ValueError, match="Pending upload"):
            await upgrade(sessions, SourceStore(tmp_path), user, apply=True)
        async with sessions() as s:
            d = await s.get(Document, UUID(value["document"]["id"]))
            assert d.pending_version_id == UUID(new["version"]["id"])
            kb = await s.get(KnowledgeBase, d.knowledge_base_id)
            assert kb.build_configuration["index_layout"] == "dense-cosine-v1"

    asyncio.run(database_call(check))
