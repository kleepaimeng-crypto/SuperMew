"""从 Catalog 获取已发布、属于当前用户的固定检索范围。候选不进入此入口。"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from minimew.documents.artifacts import read_artifact
from minimew.models import Document, DocumentVersion, IndexArtifact, IndexJob
from minimew.vector_store.adapter import CandidateIndex, VerifiedCandidate
from minimew.vector_store.manifest import CandidateManifest, VectorError


@dataclass(frozen=True)
class RetrievalScope:
    user_id: UUID
    targets: tuple[CandidateManifest, ...]
    expires_at: datetime


async def published_scope(sessions, user_id: UUID) -> RetrievalScope:
    async with sessions() as session, session.begin():
        # 一个 SELECT/MVCC 快照同时读取 current、版本和产物，避免跨次查询混版。
        rows = (
            await session.execute(
                select(DocumentVersion, IndexArtifact)
                .select_from(Document)
                .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
                .join(IndexJob, IndexJob.version_id == DocumentVersion.id)
                .outerjoin(
                    IndexArtifact,
                    (IndexArtifact.job_id == IndexJob.id) & (IndexArtifact.status == "published"),
                )
                .where(Document.user_id == user_id, DocumentVersion.status == "published")
                .order_by(Document.id)
            )
        ).all()
        now = await session.scalar(select(func.clock_timestamp()))
        targets = []
        for version, artifact in rows:
            if artifact is None:
                raise VectorError("INDEX_ARTIFACT_INVALID")
            manifest = read_artifact(artifact)
            if (
                manifest.scope.identity.user_id != user_id
                or manifest.scope.identity.version_id != version.id
            ):
                raise VectorError("INDEX_ARTIFACT_INVALID")
            targets.append(manifest)
        # 严格短于旧 current 的一小时保留期，为已取得 scope 的请求留出完成窗口。
        return RetrievalScope(user_id, tuple(targets), now + timedelta(minutes=55))


async def search_published(
    sessions, index: CandidateIndex, scope: RetrievalScope, user_id, vector, profile, *, top_k=3
):
    if scope.user_id != user_id or any(t.scope.identity.user_id != user_id for t in scope.targets):
        raise PermissionError("Retrieval scope owner mismatch")
    async with sessions() as session:
        now = await session.scalar(select(func.clock_timestamp()))
    if now >= scope.expires_at:
        raise VectorError("INDEX_SCOPE_EXPIRED")
    deadline = time.monotonic() + (scope.expires_at - now).total_seconds()
    hits = []
    for manifest in scope.targets:
        if time.monotonic() >= deadline:
            raise VectorError("INDEX_SCOPE_EXPIRED")
        hits.extend(
            await asyncio.to_thread(
                index.search, VerifiedCandidate(manifest), vector, profile, top_k=top_k
            )
        )
    return sorted(hits, key=lambda row: row["score"], reverse=True)[:top_k]
