"""独立索引进程；一个在途构建、独立 DB 心跳、有限重试、精确清理。"""

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from uuid import UUID, uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.chunking import build_version_chunks
from minimew.documents.index_cleanup import IndexCleanupRepository
from minimew.documents.jobs import IndexJobRepository, OwnershipLost
from minimew.documents.parsing import DocumentBuildError
from minimew.documents.profiles import preset
from minimew.documents.storage import SourceStore
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.embeddings.tokenizer import LocalTokenizer
from minimew.models import IndexWorkerHeartbeat
from minimew.settings import Settings
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import PROFILE, VectorError, make_candidate
from minimew.vector_store.milvus import MilvusBackend

log = logging.getLogger("minimew.indexing")
RETRYABLE = {"VECTOR_UNAVAILABLE", "VECTOR_DELETE_INCOMPLETE"}


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIMEW_INDEX_", frozen=True)
    poll_seconds: float = Field(default=1, gt=0, le=30)
    lease_seconds: float = Field(default=30, ge=1, le=300)
    max_attempts: int = Field(default=5, ge=1, le=20)
    job_timeout_seconds: float = Field(default=600, ge=10, le=3600)


class _FencedBackend:
    """每个 RPC 与 cleanup 共用 attempt advisory lock，防止删除后迟到写回。"""

    def __init__(self, worker, repo, lease, scope, lost, deadline):
        self.worker, self.repo, self.lease, self.scope = worker, repo, lease, scope
        self.lost, self.deadline = lost, deadline
        self.loop = asyncio.get_running_loop()

    def __getattr__(self, name):
        if name not in {"ensure_collection", "read", "upsert", "search", "delete"}:
            raise AttributeError(name)

        def call(*args, **kwargs):
            return asyncio.run_coroutine_threadsafe(
                self.invoke(name, args, kwargs), self.loop
            ).result()

        return call

    async def invoke(self, name, args, kwargs):
        # 不持有 Document 行锁或模型事务；只在单个有 deadline 的向量 RPC 内持锁。
        key = int.from_bytes(
            hashlib.sha256(self.scope.scope_id.encode()).digest()[:8], "big", signed=True
        )
        async with self.worker.sessions() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            if self.lost.is_set() or time.monotonic() >= self.deadline:
                raise OwnershipLost
            if isinstance(self.repo, IndexJobRepository):
                await self.repo.require(self.lease)
                manifest = await self.repo.artifact(self.lease, self.scope.attempt_id)
            else:
                manifest = await self.repo.manifest(self.lease)
            if manifest is None or manifest.scope != self.scope:
                raise OwnershipLost
            return await asyncio.to_thread(getattr(self.worker.backend, name), *args, **kwargs)


class IndexWorker:
    def __init__(
        self,
        sessions,
        store,
        tokenizer,
        encoder,
        backend,
        *,
        settings=None,
        worker_id=None,
        batch_size=4,
        vector_batch_size=4,
    ):
        self.sessions, self.store = sessions, store
        self.tokenizer, self.encoder, self.backend = tokenizer, encoder, backend
        self.settings = settings or WorkerSettings()
        self.worker_id = worker_id or f"{socket.gethostname()[:80]}:{os.getpid()}:{uuid4().hex}"
        self.batch_size = batch_size
        self.vector_batch_size = vector_batch_size
        self.jobs, self.cleanup = IndexJobRepository(sessions), IndexCleanupRepository(sessions)
        self.fingerprints = {
            preset(PROFILE.id).model_copy(update={"source_format": fmt}).fingerprint()
            for fmt in ("markdown", "text")
        }
        self.stop = asyncio.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="minimew-index")

    async def sync(self, operation, *args):
        return await asyncio.get_running_loop().run_in_executor(
            self.pool, partial(operation, *args)
        )

    async def heartbeat(self, status="running"):
        async with self.sessions() as session, session.begin():
            stmt = insert(IndexWorkerHeartbeat).values(
                worker_id=self.worker_id,
                fingerprints=sorted(self.fingerprints),
                status=status,
                seen_at=func.clock_timestamp(),
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["worker_id"],
                    set_={
                        "fingerprints": stmt.excluded.fingerprints,
                        "status": status,
                        "seen_at": func.clock_timestamp(),
                    },
                )
            )

    async def _pulse(self, repo, lease, done, lost, deadline):
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=self.settings.lease_seconds / 3)
            except TimeoutError:
                try:
                    if time.monotonic() >= deadline or not await repo.heartbeat(
                        lease, lease_seconds=self.settings.lease_seconds
                    ):
                        lost.set()
                        return
                    await self.heartbeat()
                except SQLAlchemyError:
                    lost.set()
                    log.warning("INDEX_HEARTBEAT_UNAVAILABLE")
                    return

    def _index(self, repo, lease, scope, lost, deadline):
        return CandidateIndex(
            _FencedBackend(self, repo, lease, scope, lost, deadline), self.vector_batch_size
        )

    async def _build(self, lease, lost, deadline):
        async def guard():
            if lost.is_set() or time.monotonic() >= deadline:
                raise OwnershipLost
            await self.jobs.require(lease)

        if lease.staged:
            if lease.artifact_id is None:
                raise VectorError("INDEX_ARTIFACT_INVALID")
            manifest = await self.jobs.artifact(lease, lease.artifact_id)
            index = self._index(self.jobs, lease, manifest.scope, lost, deadline)
            verified = await self.sync(index.verify, manifest)
        else:
            await guard()
            if not await self.jobs.progress(lease, "parsing", 0, None):
                raise OwnershipLost
            chunks = await self.sync(
                build_version_chunks,
                self.store,
                lease.identity,
                lease.content_sha256,
                lease.build,
                self.tokenizer,
            )
            await guard()
            count = len(chunks.chunks)
            await self.jobs.progress(lease, "chunking", count, count)
            vectors = []
            await self.jobs.progress(lease, "embedding", 0, count)
            for start in range(0, count, self.batch_size):
                await guard()
                batch = chunks.chunks[start : start + self.batch_size]
                vectors.extend(
                    await self.sync(
                        self.encoder.encode_documents, [c.embedding_text for c in batch]
                    )
                )
                await guard()
                await self.jobs.progress(lease, "embedding", len(vectors), count)
            candidate = await self.sync(make_candidate, chunks, lease.build, vectors, uuid4())
            await guard()
            await self.jobs.prepare(lease, chunks, candidate)
            await self.jobs.progress(lease, "indexing", 0, count)
            index = self._index(self.jobs, lease, candidate.manifest.scope, lost, deadline)
            verified = await self.sync(index.stage, candidate)
            await self.jobs.progress(lease, "verifying", count, count)
            await guard()
            await self.jobs.stage(lease, verified)
        await guard()
        # stage() 已完整核验；恢复路径 verify() 使用 PG 原清单，无重新编码。
        await self.jobs.publish(lease, verified)

    async def run_once(self):
        if self.stop.is_set():
            return False
        lease = await self.jobs.claim(
            self.worker_id, self.fingerprints, lease_seconds=self.settings.lease_seconds
        )
        if lease is None:
            return await self.clean_once()
        done, lost = asyncio.Event(), asyncio.Event()
        deadline = time.monotonic() + self.settings.job_timeout_seconds
        pulse = asyncio.create_task(self._pulse(self.jobs, lease, done, lost, deadline))
        try:
            if lease.attempt > self.settings.max_attempts:
                await self.jobs.fail(lease, "BUILD_FAILED", dead_letter=True)
            else:
                await self._build(lease, lost, deadline)
        except OwnershipLost:
            log.info("INDEX_OWNERSHIP_LOST job=%s", lease.job_id)
        except (DocumentBuildError, EmbeddingError, VectorError) as exc:
            code = exc.code
            log.warning("%s job=%s", code, lease.job_id)
            if code in RETRYABLE and lease.attempt < self.settings.max_attempts:
                await self.jobs.retry(lease, code, delay=min(2**lease.attempt, 60))
            else:
                public_code = (
                    "SOURCE_UNAVAILABLE"
                    if code == "DOCUMENT_SOURCE_UNAVAILABLE"
                    else "INVALID_SOURCE"
                    if isinstance(exc, DocumentBuildError)
                    else "BUILD_FAILED"
                )
                await self.jobs.fail(lease, public_code, dead_letter=code in RETRYABLE)
        except SQLAlchemyError:
            # 发布 commit 回包不确定：retry() 再查原 Job 资格；已成功则不改写。
            log.warning("INDEX_CATALOG_UNAVAILABLE job=%s", lease.job_id)
            try:
                await self.jobs.retry(
                    lease, "INDEX_CATALOG_UNAVAILABLE", delay=min(2**lease.attempt, 60)
                )
            except SQLAlchemyError:
                pass  # 无法写库时保留 lease，过期后按持久事实恢复。
        finally:
            done.set()
            await pulse
        # 即使持续有上传，也为清理队列提供一次公平机会。
        await self.clean_once()
        return True

    async def clean_once(self):
        if self.stop.is_set():
            return False
        lease = await self.cleanup.claim(lease_seconds=self.settings.lease_seconds)
        if lease is None:
            return False
        done, lost = asyncio.Event(), asyncio.Event()
        deadline = time.monotonic() + self.settings.job_timeout_seconds
        pulse = asyncio.create_task(self._pulse(self.cleanup, lease, done, lost, deadline))
        try:
            if lease.attempt > self.settings.max_attempts:
                await self.cleanup.finish(lease, error="INDEX_CLEANUP_EXHAUSTED", dead_letter=True)
                return True
            manifest = await self.cleanup.manifest(lease)
            if manifest is None:
                raise OwnershipLost
            index = self._index(self.cleanup, lease, manifest.scope, lost, deadline)
            await self.sync(index.delete_candidate, manifest.scope)
            await self.cleanup.finish(lease)
        except OwnershipLost:
            pass
        except VectorError as exc:
            await self.cleanup.finish(
                lease,
                error=exc.code,
                delay=min(2**lease.attempt, 60),
                dead_letter=lease.attempt >= self.settings.max_attempts
                or exc.code not in RETRYABLE,
            )
        finally:
            done.set()
            await pulse
        return True

    async def serve(self, *, once=False):
        try:
            while not self.stop.is_set():
                try:
                    await self.heartbeat()
                    await self.run_once()
                except SQLAlchemyError:
                    log.warning("INDEX_CATALOG_UNAVAILABLE")
                if once:
                    break
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.settings.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            self.pool.shutdown(wait=True)
            await self.heartbeat("stopped")


async def main(args):
    settings = Settings()
    engine = create_engine(settings)
    backend = worker = None
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text("SELECT version_num FROM alembic_version")) != "0013":
                raise SystemExit("Run MiniMew alembic upgrade head before starting the worker")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        if args.requeue_cleanup:
            await IndexCleanupRepository(sessions).requeue(args.requeue_cleanup)
            return
        runtime = EmbeddingRuntimeSettings()
        tokenizer = await asyncio.to_thread(LocalTokenizer.load, PROFILE, runtime)
        encoder = await asyncio.to_thread(LocalEmbeddingAdapter.load_cpu, PROFILE, runtime)
        vector_settings = VectorSettings()
        backend = await asyncio.to_thread(MilvusBackend.connect, vector_settings)
        await asyncio.to_thread(backend.ensure_collection)
        worker = IndexWorker(
            sessions,
            SourceStore(settings.document_upload_dir),
            tokenizer,
            encoder,
            backend,
            batch_size=runtime.batch_size,
            vector_batch_size=vector_settings.batch_size,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(worker.stop.set))
        await worker.serve(once=args.once)
    finally:
        if backend:
            await asyncio.to_thread(backend.close)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="最多领取一个构建及一个清理任务后退出")
    parser.add_argument("--requeue-cleanup", type=UUID, metavar="ARTIFACT_ID")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(main(parser.parse_args()))
    except (EmbeddingError, VectorError) as exc:
        raise SystemExit(exc.code) from None
