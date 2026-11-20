"""Index Job 的短事务：领取、产物登记、恢复、原子发布与失败。"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from minimew.documents.artifacts import artifact_values, read_artifact, retire_artifacts
from minimew.documents.chunking import BuildIdentity
from minimew.documents.profiles import BuildConfiguration, read_build
from minimew.models import (
    Document,
    DocumentVersion,
    EmbeddingRebuild,
    IndexArtifact,
    IndexJob,
    KnowledgeBase,
)
from minimew.vector_store.manifest import VectorError

STAGES = ("reserved", "parsing", "chunking", "embedding", "indexing", "verifying", "publishing")
ERRORS = {"INVALID_SOURCE", "SOURCE_UNAVAILABLE", "BUILD_FAILED"}


@dataclass(frozen=True)
class IndexLease:
    job_id: UUID
    document_id: UUID
    version_id: UUID
    owner_token: UUID
    execution_fence: int
    build: BuildConfiguration
    content_sha256: str
    identity: BuildIdentity
    attempt: int
    artifact_id: UUID | None = None
    staged: bool = False
    source_version_id: UUID | None = None
    credential_id: UUID | None = None


class OwnershipLost(Exception):
    pass


class IndexJobRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def claim(self, worker_id: str, fingerprints: set[str], *, lease_seconds=30.0):
        if not worker_id or len(worker_id) > 200 or not 0 < lease_seconds <= 300:
            raise ValueError("Invalid worker or lease")
        if not fingerprints:
            return None
        async with self.sessions() as session, session.begin():
            eligible = (
                or_(
                    IndexJob.status == "pending",
                    IndexJob.status.in_(["running", "staged"])
                    & (IndexJob.lease_until <= func.clock_timestamp()),
                )
                & or_(
                    IndexJob.next_retry_at.is_(None),
                    IndexJob.next_retry_at <= func.clock_timestamp(),
                )
                & (IndexJob.stage != "ready")
            )
            # 所有路径采用 Document -> Job 锁顺序；不同文档可被不同 worker 领取。
            document = await session.scalar(
                select(Document)
                .join(IndexJob, IndexJob.document_id == Document.id)
                .join(DocumentVersion, DocumentVersion.id == IndexJob.version_id)
                .where(
                    eligible,
                    Document.pending_version_id == IndexJob.version_id,
                    DocumentVersion.status == "candidate",
                    DocumentVersion.publication_fence == Document.publication_fence,
                    DocumentVersion.build_fingerprint.in_(fingerprints),
                )
                .order_by(IndexJob.created_at, IndexJob.id)
                .limit(1)
                .with_for_update(of=Document, skip_locked=True)
            )
            if document is None:
                return None
            job = await session.scalar(
                select(IndexJob)
                .where(
                    IndexJob.document_id == document.id,
                    IndexJob.version_id == document.pending_version_id,
                    eligible,
                )
                .with_for_update()
            )
            if job is None:
                return None
            version = await session.get(DocumentVersion, job.version_id)
            build = read_build(version.build_configuration, version.build_fingerprint)
            source = await session.get(EmbeddingRebuild, job.rebuild_id) if job.rebuild_id else None
            kb = await session.get(KnowledgeBase, job.knowledge_base_id)
            now = await session.scalar(select(func.clock_timestamp()))
            artifact_id = None
            if job.status == "staged":
                artifact_id = await session.scalar(
                    select(IndexArtifact.id).where(
                        IndexArtifact.job_id == job.id, IndexArtifact.status == "staged"
                    )
                )
                # 缺清单不回退为重建；交给 owner 明确失败，禁止伪造恢复。
            else:
                await retire_artifacts(session, job.id)
                job.status = "running"
                job.stage, job.progress_done, job.progress_total = "reserved", 0, None
            job.worker_id, job.owner_token = worker_id, uuid4()
            job.execution_fence += 1
            job.attempt += 1
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.next_retry_at = None
            return IndexLease(
                job.id,
                document.id,
                version.id,
                job.owner_token,
                job.execution_fence,
                build,
                version.content_sha256,
                BuildIdentity(job.user_id, job.knowledge_base_id, document.id, version.id),
                job.attempt,
                artifact_id,
                job.status == "staged",
                job.source_version_id,
                source.credential_id if source else kb.embedding_credential_id,
            )

    async def _owned(self, session, lease):
        document = await session.scalar(
            select(Document).where(Document.id == lease.document_id).with_for_update()
        )
        if document is None or document.pending_version_id != lease.version_id:
            return None
        job = await session.scalar(
            select(IndexJob)
            .where(
                IndexJob.id == lease.job_id,
                IndexJob.document_id == document.id,
                IndexJob.version_id == lease.version_id,
                IndexJob.status.in_(["running", "staged"]),
                IndexJob.stage != "ready",
                IndexJob.owner_token == lease.owner_token,
                IndexJob.execution_fence == lease.execution_fence,
                IndexJob.lease_until > func.clock_timestamp(),
            )
            .with_for_update()
        )
        if job is None:
            return None
        version = await session.get(DocumentVersion, lease.version_id)
        if version.status != "candidate" or version.publication_fence != document.publication_fence:
            return None
        return document, job

    async def check(self, lease):
        async with self.sessions() as session, session.begin():
            return await self._owned(session, lease) is not None

    async def require(self, lease):
        if not await self.check(lease):
            raise OwnershipLost

    async def heartbeat(self, lease: IndexLease, *, lease_seconds=30.0) -> bool:
        if not 0 < lease_seconds <= 300:
            raise ValueError("Invalid lease")
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                return False
            now = await session.scalar(select(func.clock_timestamp()))
            owned[1].lease_until = now + timedelta(seconds=lease_seconds)
        return True

    async def progress(self, lease: IndexLease, stage: str, done: int, total: int | None) -> bool:
        if stage not in STAGES or done < 0 or (total is not None and total < done):
            raise ValueError("Invalid progress")
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                return False
            job = owned[1]
            if job.status != "running":
                raise ValueError("Staged job cannot rebuild")
            if STAGES.index(stage) < STAGES.index(job.stage) or (
                stage == job.stage
                and (
                    done < job.progress_done
                    or (job.progress_total is not None and total != job.progress_total)
                )
            ):
                raise ValueError("Progress cannot regress")
            job.stage, job.progress_done, job.progress_total = stage, done, total
        return True

    async def fail(
        self, lease: IndexLease, error_code: str, *, dead_letter=False, cause=None
    ) -> bool:
        if error_code not in ERRORS:
            raise ValueError("Unknown public failure code")
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                return False
            document, job = owned
            version = await session.get(DocumentVersion, lease.version_id)
            version.status = "failed"
            document.pending_version_id = None
            await retire_artifacts(session, job.id)
            job.status, job.error_code = ("dead_letter" if dead_letter else "failed"), error_code
            if cause is not None:
                job.last_error_code = cause
            job.finished_at = await session.scalar(select(func.clock_timestamp()))
        return True

    async def retry(self, lease, code, *, delay):
        if not 0 <= delay <= 300 or not code.isupper() or len(code) > 64:
            raise ValueError("Invalid retry")
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                return False
            now = await session.scalar(select(func.clock_timestamp()))
            job = owned[1]
            job.next_retry_at = now + timedelta(seconds=delay)
            job.lease_until = now
            job.last_error_code = code
        return True

    async def prepare(self, lease, chunks, candidate):
        if (
            chunks.identity != lease.identity
            or chunks.build_fingerprint != lease.build.fingerprint()
            or chunks.source_sha256 != lease.content_sha256
            or candidate.manifest.scope.identity != lease.identity
        ):
            raise ValueError("Candidate does not belong to lease")
        values = artifact_values(chunks, candidate.manifest)
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                raise OwnershipLost
            if owned[1].status != "running":
                raise ValueError("Staged job cannot prepare")
            artifact = IndexArtifact(
                id=candidate.manifest.scope.attempt_id,
                job_id=lease.job_id,
                execution_fence=lease.execution_fence,
                **values,
            )
            session.add(artifact)
            await session.flush()
            read_artifact(artifact)
        return artifact.id

    async def artifact(self, lease, artifact_id):
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                raise OwnershipLost
            row = await session.get(IndexArtifact, artifact_id)
            if row is None or row.job_id != lease.job_id or row.status == "retired":
                raise VectorError("INDEX_ARTIFACT_INVALID")
            manifest = read_artifact(row)
            if (
                manifest.scope.identity != lease.identity
                or manifest.scope.build_fingerprint != lease.build.fingerprint()
                or manifest.scope.source_sha256 != lease.content_sha256
            ):
                raise VectorError("INDEX_ARTIFACT_INVALID")
            return manifest

    async def stage(self, lease, verified):
        manifest = verified.manifest
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                raise OwnershipLost
            artifact = await session.get(IndexArtifact, manifest.scope.attempt_id)
            if (
                artifact is None
                or artifact.job_id != lease.job_id
                or artifact.status not in ("prepared", "staged")
                or read_artifact(artifact) != manifest
            ):
                raise VectorError("INDEX_ARTIFACT_INVALID")
            artifact.status = "staged"
            job = owned[1]
            job.status, job.stage = "staged", "publishing"
            job.progress_done = job.progress_total = len(manifest.entries)

    async def publish(self, lease, verified):
        """Milvus 已精确重验；只在此事务中切换检索事实。"""
        async with self.sessions() as session, session.begin():
            owned = await self._owned(session, lease)
            if owned is None:
                raise OwnershipLost
            document, job = owned
            artifact = await session.get(IndexArtifact, verified.manifest.scope.attempt_id)
            if (
                job.status != "staged"
                or artifact is None
                or artifact.job_id != job.id
                or artifact.status != "staged"
                or read_artifact(artifact) != verified.manifest
            ):
                raise VectorError("INDEX_ARTIFACT_INVALID")
            if job.rebuild_id is not None:
                job.stage = "ready"
            else:
                now = await session.scalar(select(func.clock_timestamp()))
                await publish_candidate(session, document, job, artifact, now)
        return True


async def publish_candidate(session, document, job, artifact, now):
    if document.current_version_id:
        previous = await session.get(DocumentVersion, document.current_version_id)
        previous.status = "superseded"
        old_job = await session.scalar(select(IndexJob).where(IndexJob.version_id == previous.id))
        await retire_artifacts(session, old_job.id, grace_seconds=3600)
    version = await session.get(DocumentVersion, job.version_id)
    version.status = "published"
    document.current_version_id, document.pending_version_id = version.id, None
    artifact.status = "published"
    job.status, job.error_code, job.last_error_code = "succeeded", None, None
    job.finished_at = now
