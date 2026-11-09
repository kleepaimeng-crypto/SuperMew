"""持久 Index Job 领取 seam；本包不启动消费 loop 或提供假构建 handler。"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from minimew.documents.profiles import BuildConfiguration, read_build
from minimew.models import Document, DocumentVersion, IndexJob

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


class IndexJobRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def claim(self, worker_id: str, fingerprints: set[str], *, lease_seconds=30.0):
        if not worker_id or len(worker_id) > 200 or not 0 < lease_seconds <= 300:
            raise ValueError("Invalid worker or lease")
        if not fingerprints:
            return None
        async with self.sessions() as session, session.begin():
            eligible = or_(
                IndexJob.status == "pending",
                (IndexJob.status == "running") & (IndexJob.lease_until <= func.clock_timestamp()),
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
            now = await session.scalar(select(func.clock_timestamp()))
            job.status = "running"
            job.stage, job.progress_done, job.progress_total = "reserved", 0, None
            job.worker_id, job.owner_token = worker_id, uuid4()
            job.execution_fence += 1
            job.attempt += 1
            job.lease_until = now + timedelta(seconds=lease_seconds)
            return IndexLease(
                job.id,
                document.id,
                version.id,
                job.owner_token,
                job.execution_fence,
                build,
                version.content_sha256,
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
                IndexJob.status == "running",
                IndexJob.owner_token == lease.owner_token,
                IndexJob.execution_fence == lease.execution_fence,
                IndexJob.lease_until > func.clock_timestamp(),
            )
            .with_for_update()
        )
        return (document, job) if job is not None else None

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

    async def fail(self, lease: IndexLease, error_code: str) -> bool:
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
            job.status, job.error_code = "failed", error_code
            job.finished_at = await session.scalar(select(func.clock_timestamp()))
        return True
