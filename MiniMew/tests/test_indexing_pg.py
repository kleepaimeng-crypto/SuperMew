"""真实 PG 事务/锁/恢复，向量和编码故障注入；真实模型进程另行验收。"""

import asyncio
import os
import time
from dataclasses import replace
from datetime import timedelta
from threading import Event
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from test_document_chunking import CharacterTokenizer
from test_documents_pg import account, database_call, upload
from test_documents_pg import client as document_client
from test_vector_store import FakeBackend

from minimew.documents.artifacts import read_artifact
from minimew.documents.chunking import build_version_chunks
from minimew.documents.index_cleanup import IndexCleanupRepository
from minimew.documents.jobs import IndexJobRepository, OwnershipLost
from minimew.documents.retrieval import published_scope, search_published
from minimew.documents.storage import SourceStore
from minimew.documents.worker import IndexWorker, WorkerSettings
from minimew.models import Document, IndexArtifact, IndexCleanupJob, IndexJob, IndexWorkerHeartbeat
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import PROFILE, VectorError, make_candidate

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit owned temporary PostgreSQL required",
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    yield from document_client.__wrapped__(monkeypatch, tmp_path)


class Encoder:
    def __init__(self):
        self.calls = 0

    def encode_documents(self, texts):
        self.calls += 1
        return [[1.0] + [0.0] * 511 for _ in texts]


def worker(sessions, tmp_path, backend=None, encoder=None, **kwargs):
    return IndexWorker(
        sessions,
        SourceStore(tmp_path),
        CharacterTokenizer(),
        encoder or Encoder(),
        backend or FakeBackend(),
        **kwargs,
    )


async def stage(sessions, tmp_path, value, backend=None):
    repo = IndexJobRepository(sessions)
    lease = await repo.claim("staging-fixture", {value["version"]["build_fingerprint"]})
    assert lease
    chunks = build_version_chunks(
        SourceStore(tmp_path),
        lease.identity,
        lease.content_sha256,
        lease.build,
        CharacterTokenizer(),
    )
    candidate = make_candidate(
        chunks,
        lease.build,
        Encoder().encode_documents([c.embedding_text for c in chunks.chunks]),
        uuid4(),
    )
    await repo.prepare(lease, chunks, candidate)
    backend = backend or FakeBackend()
    verified = CandidateIndex(backend).stage(candidate)
    await repo.stage(lease, verified)
    return repo, lease, candidate, verified, backend


def test_pending_invisible_worker_publish_and_owner_scope(client, tmp_path):
    headers, owner = account(client)
    value = upload(client, headers, content="# 制度\n餐费150元。".encode()).json()

    async def scenario(sessions):
        assert (await published_scope(sessions, owner)).targets == ()
        w = worker(sessions, tmp_path)
        try:
            assert await w.run_once()
            scope = await published_scope(sessions, owner)
            assert len(scope.targets) == 1
            assert scope.targets[0].scope.identity.version_id == UUID(value["version"]["id"])
            assert (await published_scope(sessions, uuid4())).targets == ()
            hits = await search_published(
                sessions, CandidateIndex(w.backend), scope, owner, [1.0] + [0.0] * 511, PROFILE
            )
            assert "150" in hits[0]["payload"]["source_text"]
            with pytest.raises(PermissionError):
                await search_published(
                    sessions,
                    CandidateIndex(w.backend),
                    scope,
                    uuid4(),
                    [1.0] + [0.0] * 511,
                    PROFILE,
                )
            with pytest.raises(VectorError, match="INDEX_SCOPE_EXPIRED"):
                await search_published(
                    sessions,
                    CandidateIndex(w.backend),
                    replace(scope, expires_at=scope.expires_at - timedelta(hours=1)),
                    owner,
                    [1.0] + [0.0] * 511,
                    PROFILE,
                )
            assert not await w.run_once()
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))
    assert (
        client.get("/v1/index-jobs/" + value["job"]["id"], headers=headers).json()["status"]
        == "succeeded"
    )


def test_staged_reclaim_reuses_manifest_without_encoding(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, old, candidate, verified, backend = await stage(sessions, tmp_path, value)
        assert await repo.retry(old, "INDEX_CATALOG_UNAVAILABLE", delay=0)
        encoder = Mock()
        encoder.encode_documents.side_effect = AssertionError("Must not encode staged recovery")
        w = worker(sessions, tmp_path, backend, encoder)
        try:
            assert await w.run_once()
            encoder.encode_documents.assert_not_called()
            assert backend.writes == 1
            async with sessions() as session:
                job = await session.get(IndexJob, old.job_id)
                artifact = await session.get(IndexArtifact, candidate.manifest.scope.attempt_id)
                assert job.status == "succeeded" and job.attempt == 2
                assert read_artifact(artifact) == candidate.manifest
                assert artifact.status == "published"
            with pytest.raises(OwnershipLost):
                await repo.publish(old, verified)
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


@pytest.mark.parametrize("after_commit", [False, True])
def test_publication_commit_failure_and_uncertain_response(client, tmp_path, after_commit):
    headers, owner = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, _, verified, backend = await stage(sessions, tmp_path, value)
        async with sessions() as failing_session:

            def failure(*args):
                raise OperationalError("commit", {}, Exception("controlled connection failure"))

            event.listen(
                failing_session.sync_session,
                "after_commit" if after_commit else "before_commit",
                failure,
            )
            failing_repo = IndexJobRepository(lambda: failing_session)
            with pytest.raises(OperationalError):
                await failing_repo.publish(lease, verified)
        async with sessions() as session:
            job = await session.get(IndexJob, lease.job_id)
            doc = await session.get(Document, lease.document_id)
            assert job.status == ("succeeded" if after_commit else "staged")
            assert (doc.current_version_id == lease.version_id) == after_commit
        # 不确定提交后只能查询持久资格；已成功不能改回 retry/failed。
        assert await repo.retry(lease, "INDEX_CATALOG_UNAVAILABLE", delay=0) == (not after_commit)
        if not after_commit:
            w = worker(sessions, tmp_path, backend)
            try:
                await w.run_once()
            finally:
                w.pool.shutdown()
        assert len((await published_scope(sessions, owner)).targets) == 1

    asyncio.run(database_call(scenario))


def test_latest_upload_cancels_staged_candidate_and_blocks_late_publish(client, tmp_path):
    headers, owner = account(client)
    value = upload(client, headers).json()
    saved = {}

    async def first(sessions):
        saved["staged"] = await stage(sessions, tmp_path, value)

    asyncio.run(database_call(first))
    newer = upload(
        client, headers, document=value["document"]["id"], key="newer", content=b"replacement"
    ).json()

    async def last(sessions):
        _, lease, candidate, verified, backend = saved["staged"]
        repo = IndexJobRepository(sessions)
        with pytest.raises(OwnershipLost):
            await repo.publish(lease, verified)
        w = worker(sessions, tmp_path, backend)
        try:
            await w.run_once()
            assert (await published_scope(sessions, owner)).targets[
                0
            ].scope.identity.version_id == UUID(newer["version"]["id"])
            assert backend.read(candidate.manifest.scope, 1) == []
            assert SourceStore(tmp_path).path(lease.version_id).exists()
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(last))


def test_new_version_failure_leaves_old_published_retrievable(client, tmp_path):
    headers, owner = account(client)
    first = upload(client, headers, content=b"old stable").json()
    backend = FakeBackend()

    async def publish(sessions):
        w = worker(sessions, tmp_path, backend)
        try:
            await w.run_once()
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(publish))
    second = upload(
        client, headers, document=first["document"]["id"], key="bad", content=b"new source"
    ).json()
    SourceStore(tmp_path).path(UUID(second["version"]["id"])).write_bytes(b"corrupted")
    asyncio.run(database_call(publish))

    async def check(sessions):
        assert (await published_scope(sessions, owner)).targets[
            0
        ].scope.identity.version_id == UUID(first["version"]["id"])
        async with sessions() as session:
            assert (await session.get(IndexJob, UUID(second["job"]["id"]))).status == "failed"

    asyncio.run(database_call(check))


def test_old_scope_grace_and_exact_cleanup_preserves_current(client, tmp_path):
    headers, owner = account(client)
    first = upload(client, headers).json()
    backend, saved = FakeBackend(), {}

    async def publish(sessions):
        w = worker(sessions, tmp_path, backend)
        try:
            await w.run_once()
            saved.setdefault("old", await published_scope(sessions, owner))
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(publish))
    second = upload(
        client, headers, document=first["document"]["id"], key="v2", content=b"new"
    ).json()
    asyncio.run(database_call(publish))

    async def check(sessions):
        cleanup = IndexCleanupRepository(sessions)
        assert await cleanup.claim() is None
        hits = await search_published(
            sessions, CandidateIndex(backend), saved["old"], owner, [1.0] + [0.0] * 511, PROFILE
        )
        assert hits
        async with sessions() as session, session.begin():
            row = await session.get(IndexCleanupJob, saved["old"].targets[0].scope.attempt_id)
            now = await session.scalar(select(func.clock_timestamp()))
            assert 3590 < (row.due_at - now).total_seconds() <= 3600
            # 控制测试时钟 fixture，不等待一小时，不伪装成实际经过一小时。
            row.due_at = now
        w = worker(sessions, tmp_path, backend)
        try:
            assert await w.clean_once()
            assert backend.read(saved["old"].targets[0].scope, 1) == []
            current = await published_scope(sessions, owner)
            assert current.targets[0].scope.identity.version_id == UUID(second["version"]["id"])
            CandidateIndex(backend).verify(current.targets[0])
            assert not await w.clean_once()
            assert SourceStore(tmp_path).path(UUID(first["version"]["id"])).exists()
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(check))


def test_unstaged_crash_retires_partial_namespace_and_rebuilds(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("crashed", {value["version"]["build_fingerprint"]})
        chunks = build_version_chunks(
            SourceStore(tmp_path),
            lease.identity,
            lease.content_sha256,
            lease.build,
            CharacterTokenizer(),
        )
        candidate = make_candidate(
            chunks, lease.build, Encoder().encode_documents(["hello"]), uuid4()
        )
        await repo.prepare(lease, chunks, candidate)
        backend = FakeBackend()
        backend.upsert([candidate.records[0].entity(candidate.manifest.scope)])
        assert await repo.retry(lease, "VECTOR_UNAVAILABLE", delay=0)
        w = worker(sessions, tmp_path, backend)
        try:
            await w.run_once()
            assert backend.read(candidate.manifest.scope, 1) == []
            async with sessions() as session:
                job = await session.get(IndexJob, lease.job_id)
                assert job.status == "succeeded" and job.attempt == 2
                assert await session.scalar(select(func.count()).select_from(IndexArtifact)) == 2
            assert not await repo.fail(lease, "BUILD_FAILED")
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_staged_corruption_fails_without_reencoding_or_publishing(client, tmp_path):
    headers, owner = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, _, _, backend = await stage(sessions, tmp_path, value)
        backend.rows.clear()
        await repo.retry(lease, "INDEX_CATALOG_UNAVAILABLE", delay=0)
        encoder = Mock()
        w = worker(sessions, tmp_path, backend, encoder)
        try:
            await w.run_once()
            encoder.encode_documents.assert_not_called()
            assert (await published_scope(sessions, owner)).targets == ()
            async with sessions() as session:
                assert (await session.get(IndexJob, lease.job_id)).status == "failed"
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_retry_backoff_and_dead_letter_are_durable(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        backend = FakeBackend()
        backend.ensure_collection = Mock(side_effect=VectorError("VECTOR_UNAVAILABLE"))
        w = worker(sessions, tmp_path, backend, settings=WorkerSettings(max_attempts=1))
        try:
            await w.run_once()
            async with sessions() as session:
                job = await session.get(IndexJob, UUID(value["job"]["id"]))
                assert job.status == "dead_letter" and job.attempt == 1
            assert not await w.jobs.claim("another", w.fingerprints)
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_live_heartbeat_keeps_slow_encoder_owned_and_drain_stops_claims(client, tmp_path):
    headers, _ = account(client)
    first = upload(client, headers).json()
    upload(client, headers, key="second-document")

    async def scenario(sessions):
        encoder = Encoder()
        encode = encoder.encode_documents
        encoder.encode_documents = lambda texts: (time.sleep(1.4), encode(texts))[1]
        w = worker(sessions, tmp_path, encoder=encoder, settings=WorkerSettings(lease_seconds=1))
        try:
            running = asyncio.create_task(w.run_once())
            await asyncio.sleep(1.1)
            # 心跳让第一份任务跨过初始 lease 仍有资格；只允许另一文档被别的 worker 领取。
            other = await w.jobs.claim("competitor", w.fingerprints)
            assert other and other.version_id != UUID(first["version"]["id"])
            w.stop.set()
            assert await running
            assert not await w.run_once()
            async with sessions() as session:
                assert (await session.get(IndexJob, UUID(first["job"]["id"]))).status == "succeeded"
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_artifact_immutable_and_retired_cannot_revive(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, candidate, _, _ = await stage(sessions, tmp_path, value)
        aid = candidate.manifest.scope.attempt_id
        with pytest.raises(IntegrityError):
            async with sessions() as session, session.begin():
                await session.execute(
                    text("UPDATE index_artifacts SET manifest='{}' WHERE id=:id"), {"id": aid}
                )
        await repo.fail(lease, "BUILD_FAILED")
        with pytest.raises(IntegrityError):
            async with sessions() as session, session.begin():
                await session.execute(
                    text("UPDATE index_artifacts SET status='published' WHERE id=:id"), {"id": aid}
                )
        async with sessions() as session:
            assert (await session.get(IndexArtifact, aid)).status == "retired"

    asyncio.run(database_call(scenario))


def test_cleanup_replays_delete_after_finalize_failure_and_rejects_stale_owner(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, candidate, _, backend = await stage(sessions, tmp_path, value)
        await repo.fail(lease, "BUILD_FAILED")
        cleanup = IndexCleanupRepository(sessions)
        old = await cleanup.claim(lease_seconds=0.1)
        CandidateIndex(backend).delete_candidate(candidate.manifest.scope)
        await asyncio.sleep(0.15)
        new = await cleanup.claim()
        assert new.execution_fence == old.execution_fence + 1
        assert not await cleanup.finish(old)
        assert not await cleanup.heartbeat(old)
        CandidateIndex(backend).delete_candidate(candidate.manifest.scope)
        assert await cleanup.finish(new)

    asyncio.run(database_call(scenario))


def test_retired_cleanup_serializes_inflight_write_and_rejects_later_writes(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, candidate, _, backend = await stage(sessions, tmp_path, value)
        w = worker(sessions, tmp_path, backend)
        scope = candidate.manifest.scope
        cleaner = worker(sessions, tmp_path, backend)
        entered, release = Event(), Event()
        upsert = backend.upsert

        def slow(rows):
            entered.set()
            assert release.wait(5)
            upsert(rows)

        backend.upsert = slow
        fenced = w._index(repo, lease, scope, asyncio.Event(), time.monotonic() + 30).backend
        try:
            writing = asyncio.create_task(
                w.sync(fenced.upsert, [candidate.records[0].entity(scope)])
            )
            assert await asyncio.to_thread(entered.wait, 3)
            await repo.fail(lease, "BUILD_FAILED")
            cleaning = asyncio.create_task(cleaner.clean_once())
            await asyncio.sleep(0.1)
            assert not cleaning.done()
            release.set()
            await writing
            assert await cleaning
            assert backend.read(scope, 1) == []
            with pytest.raises(OwnershipLost):
                await w.sync(fenced.upsert, [candidate.records[0].entity(scope)])
            assert backend.read(scope, 1) == []
        finally:
            release.set()
            await asyncio.to_thread(w.pool.shutdown)
            await asyncio.to_thread(cleaner.pool.shutdown)

    asyncio.run(database_call(scenario))


def test_publish_failure_worker_backoff_and_new_worker_publish_only(client, tmp_path):
    headers, owner = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        encoder, backend = Encoder(), FakeBackend()
        first = worker(sessions, tmp_path, backend, encoder)
        first.jobs.publish = Mock(side_effect=OperationalError("commit", {}, Exception("injected")))
        second = worker(sessions, tmp_path, backend, encoder)
        try:
            assert await first.run_once()
            calls = encoder.calls
            async with sessions() as session:
                job = await session.get(IndexJob, UUID(value["job"]["id"]))
                assert job.status == "staged" and job.last_error_code == "INDEX_CATALOG_UNAVAILABLE"
                now = await session.scalar(select(func.clock_timestamp()))
                assert 0 < (job.next_retry_at - now).total_seconds() <= 2
            assert not await second.run_once()  # 未到 retry 时间，不能偷跑。
            await asyncio.sleep(2.1)
            assert await second.run_once()
            assert encoder.calls == calls
            assert len((await published_scope(sessions, owner)).targets) == 1
        finally:
            first.pool.shutdown()
            second.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_staged_without_original_manifest_fails_closed(client, tmp_path):
    headers, owner = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("legacy-staged", {value["version"]["build_fingerprint"]})
        # 历史 0011 有 staged 枚举但尚无产物登记；不能据此编造清单或假恢复。
        async with sessions() as session, session.begin():
            job = await session.get(IndexJob, lease.job_id)
            job.status, job.stage = "staged", "publishing"
        assert await repo.retry(lease, "INDEX_CATALOG_UNAVAILABLE", delay=0)
        with pytest.raises(IntegrityError):
            async with sessions() as session, session.begin():
                await session.execute(
                    text("UPDATE index_jobs SET progress_done=1 WHERE id=:id"), {"id": lease.job_id}
                )
        encoder = Mock()
        w = worker(sessions, tmp_path, encoder=encoder)
        try:
            await w.run_once()
            encoder.encode_documents.assert_not_called()
            assert (await published_scope(sessions, owner)).targets == ()
            async with sessions() as session:
                assert (await session.get(IndexJob, lease.job_id)).status == "failed"
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_idle_worker_reports_capability_and_graceful_stop(client, tmp_path):
    async def scenario(sessions):
        w = worker(sessions, tmp_path, batch_size=2, vector_batch_size=1)
        assert w.batch_size == 2
        assert w.vector_batch_size == 1
        task = asyncio.create_task(w.serve())
        try:
            deadline = time.monotonic() + 3
            while True:
                async with sessions() as session:
                    row = await session.get(IndexWorkerHeartbeat, w.worker_id)
                if row:
                    assert row.status == "running"
                    assert set(row.fingerprints) == w.fingerprints
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.03)
        finally:
            w.stop.set()
            await task
        async with sessions() as session:
            row = await session.get(IndexWorkerHeartbeat, w.worker_id)
            assert row.status == "stopped"
        assert w.encoder.calls == 0

    asyncio.run(database_call(scenario))


def test_repeated_cleanup_crash_exhaustion_does_not_issue_another_delete(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, candidate, _, backend = await stage(sessions, tmp_path, value)
        await repo.fail(lease, "BUILD_FAILED")
        w = worker(sessions, tmp_path, backend, settings=WorkerSettings(max_attempts=1))
        try:
            assert await w.cleanup.claim(lease_seconds=0.1)
            await asyncio.sleep(0.15)  # 模拟第一次清理 owner 崩溃，没有 finalize。
            backend.delete = Mock(side_effect=AssertionError("Attempt budget exhausted"))
            assert await w.clean_once()
            backend.delete.assert_not_called()
            async with sessions() as session:
                row = await session.get(IndexCleanupJob, candidate.manifest.scope.attempt_id)
                assert row.status == "dead_letter"
                assert row.error_code == "INDEX_CLEANUP_EXHAUSTED"
            assert not await w.clean_once()
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))


def test_cleanup_dead_letter_requires_explicit_requeue_then_replays_exact_scope(client, tmp_path):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def scenario(sessions):
        repo, lease, candidate, _, backend = await stage(sessions, tmp_path, value)
        await repo.fail(lease, "BUILD_FAILED")
        w = worker(sessions, tmp_path, backend, settings=WorkerSettings(max_attempts=1))
        delete = backend.delete
        backend.delete = Mock(side_effect=VectorError("VECTOR_UNAVAILABLE"))
        aid = candidate.manifest.scope.attempt_id
        try:
            assert await w.clean_once()
            async with sessions() as session:
                row = await session.get(IndexCleanupJob, aid)
                assert row.status == "dead_letter" and row.attempt == 1
            assert not await w.clean_once()
            backend.delete = delete
            await w.cleanup.requeue(aid)
            assert await w.clean_once()
            assert not backend.read(candidate.manifest.scope, 1)
            async with sessions() as session:
                row = await session.get(IndexCleanupJob, aid)
                assert row.status == "complete" and row.attempt == 1
                assert row.execution_fence == 2
            with pytest.raises(ValueError):
                await w.cleanup.requeue(aid)
        finally:
            w.pool.shutdown()

    asyncio.run(database_call(scenario))
