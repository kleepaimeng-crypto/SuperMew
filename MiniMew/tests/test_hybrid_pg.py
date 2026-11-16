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


def test_parser_upgrade_rebuilds_blank_titles_without_mutating_old_version(client, tmp_path):
    from minimew.documents.profiles import preset

    headers, user = account(client)

    async def legacy_kb(sessions):
        async with sessions() as session, session.begin():
            build = preset("bge-small-zh-v1.5")
            session.add(
                KnowledgeBase(
                    user_id=user,
                    build_configuration=build.model_dump(mode="json"),
                    build_fingerprint=build.fingerprint(),
                )
            )

    asyncio.run(database_call(legacy_kb))
    assert (
        upload(client, headers, content="#\n餐费150元。\n##\n工作餐需扣减。".encode()).status_code
        == 202
    )

    async def scenario(sessions):
        store = SourceStore(tmp_path)
        w = worker(sessions, tmp_path, backend=HybridBackend())
        try:
            assert await w.run_once()
            old = await published_scope(sessions, user)
            old_id = old.targets[0].scope.identity.version_id
            async with sessions() as session:
                previous = await session.get(DocumentVersion, old_id)
                old_build = dict(previous.build_configuration)
                old_hash = previous.content_sha256
            original = store.read(old_id, old_hash)
            plan = await upgrade(sessions, store, user, parser="markdown-txt-v2")
            assert plan["documents"] == 1 and plan["layout"] == "dense-cosine-v1"
            applied = await upgrade(sessions, store, user, parser="markdown-txt-v2", apply=True)
            assert len(applied["jobs"]) == 1
            assert (await published_scope(sessions, user)).targets == old.targets
            assert not (await upgrade(sessions, store, user, parser="markdown-txt-v2", apply=True))[
                "jobs"
            ]
            assert await w.run_once()
            new = await published_scope(sessions, user)
            assert new.targets[0].scope.identity.version_id != old_id
            async with sessions() as session:
                previous = await session.get(DocumentVersion, old_id)
                current = await session.get(
                    DocumentVersion, new.targets[0].scope.identity.version_id
                )
                assert previous.build_configuration == old_build
                assert current.build_configuration["parser"] == "markdown-txt-v2"
                assert current.content_sha256 == old_hash
            assert store.read(old_id, old_hash) == original
            assert store.read(current.id, old_hash) == original
            assert (await upgrade(sessions, store, user, parser="markdown-txt-v2"))[
                "documents"
            ] == 0
        finally:
            w.pool.shutdown(wait=True)

    asyncio.run(database_call(scenario))


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


def test_parser_upgrade_targets_only_owned_document(client, tmp_path):
    from uuid import uuid4

    from minimew.documents.profiles import preset

    headers, user = account(client)

    async def legacy_kb(sessions):
        async with sessions() as session, session.begin():
            build = preset("bge-small-zh-v1.5")
            session.add(
                KnowledgeBase(
                    user_id=user,
                    build_configuration=build.model_dump(mode="json"),
                    build_fingerprint=build.fingerprint(),
                )
            )

    asyncio.run(database_call(legacy_kb))
    first = upload(client, headers, content="#\n餐费150元。".encode()).json()
    other = upload(client, headers, key="other", content=b"Other document").json()

    async def scenario(sessions):
        store = SourceStore(tmp_path)
        w = worker(sessions, tmp_path, backend=HybridBackend())
        try:
            assert await w.run_once()
            assert await w.run_once()
            with pytest.raises(ValueError, match="Published document not found"):
                await upgrade(sessions, store, user, parser="markdown-txt-v2", document_id=uuid4())
            result = await upgrade(
                sessions,
                store,
                user,
                apply=True,
                parser="markdown-txt-v2",
                document_id=UUID(first["document"]["id"]),
            )
            assert result["documents"] == 1
            assert result["jobs"][0]["document_id"] == first["document"]["id"]
            assert await w.run_once()
            async with sessions() as session:
                untouched = await session.get(Document, UUID(other["document"]["id"]))
                assert untouched.current_version_id == UUID(other["version"]["id"])
                assert untouched.pending_version_id is None
        finally:
            w.pool.shutdown(wait=True)

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
