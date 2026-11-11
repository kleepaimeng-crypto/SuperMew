"""退休 attempt 的持久、可重放清理；不删除原始 Version/source。"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from minimew.documents.artifacts import read_artifact
from minimew.models import IndexArtifact, IndexCleanupJob


@dataclass(frozen=True)
class CleanupLease:
    artifact_id: UUID
    owner_token: UUID
    execution_fence: int
    attempt: int


class IndexCleanupRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def claim(self, *, lease_seconds=30):
        async with self.sessions() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            row = await session.scalar(
                select(IndexCleanupJob)
                .where(
                    IndexCleanupJob.due_at <= now,
                    or_(
                        IndexCleanupJob.next_retry_at.is_(None),
                        IndexCleanupJob.next_retry_at <= now,
                    ),
                    or_(
                        IndexCleanupJob.status == "pending",
                        (IndexCleanupJob.status == "running")
                        & (IndexCleanupJob.lease_until <= now),
                    ),
                )
                .order_by(IndexCleanupJob.due_at, IndexCleanupJob.artifact_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            artifact = await session.get(IndexArtifact, row.artifact_id)
            if artifact.status != "retired":
                raise ValueError("Cleanup requires an irreversibly retired attempt")
            row.status, row.owner_token = "running", uuid4()
            row.execution_fence += 1
            row.attempt += 1
            row.lease_until = now + timedelta(seconds=lease_seconds)
            row.next_retry_at = None
            return CleanupLease(row.artifact_id, row.owner_token, row.execution_fence, row.attempt)

    async def _owned(self, session, lease):
        return await session.scalar(
            select(IndexCleanupJob)
            .where(
                IndexCleanupJob.artifact_id == lease.artifact_id,
                IndexCleanupJob.status == "running",
                IndexCleanupJob.owner_token == lease.owner_token,
                IndexCleanupJob.execution_fence == lease.execution_fence,
                IndexCleanupJob.lease_until > func.clock_timestamp(),
            )
            .with_for_update()
        )

    async def manifest(self, lease):
        async with self.sessions() as session, session.begin():
            if await self._owned(session, lease) is None:
                return None
            artifact = await session.get(IndexArtifact, lease.artifact_id)
            if artifact.status != "retired":
                raise ValueError("Active artifact cannot be cleaned")
            return read_artifact(artifact)

    async def heartbeat(self, lease, *, lease_seconds=30):
        async with self.sessions() as session, session.begin():
            row = await self._owned(session, lease)
            if row is None:
                return False
            row.lease_until = await session.scalar(select(func.clock_timestamp())) + timedelta(
                seconds=lease_seconds
            )
        return True

    async def finish(self, lease, *, error=None, delay=0, dead_letter=False):
        async with self.sessions() as session, session.begin():
            row = await self._owned(session, lease)
            if row is None:
                return False
            now = await session.scalar(select(func.clock_timestamp()))
            if error is None:
                row.status, row.error_code, row.finished_at = "complete", None, now
            elif dead_letter:
                row.status, row.error_code, row.finished_at = "dead_letter", error, now
            else:
                row.error_code, row.lease_until = error, now
                row.next_retry_at = now + timedelta(seconds=delay)
        return True

    async def requeue(self, artifact_id):
        """操作员显式重排 dead-letter，保持 exact attempt，不能恢复索引发布。"""
        async with self.sessions() as session, session.begin():
            row = await session.get(IndexCleanupJob, artifact_id, with_for_update=True)
            artifact = await session.get(IndexArtifact, artifact_id)
            if row is None or row.status != "dead_letter" or artifact.status != "retired":
                raise ValueError("Only a retired cleanup dead-letter may be requeued")
            row.status, row.error_code, row.finished_at = "pending", None, None
            row.attempt = 0
            row.next_retry_at = await session.scalar(select(func.clock_timestamp()))
