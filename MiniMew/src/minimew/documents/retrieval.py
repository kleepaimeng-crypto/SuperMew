"""从 Catalog 获取已发布、属于当前用户的固定检索范围。候选不进入此入口。"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import func, select

from minimew.documents.artifacts import read_artifact
from minimew.documents.chunking import ChunkBuild
from minimew.models import Document, DocumentVersion, IndexArtifact, IndexJob, KnowledgeBase, Thread
from minimew.vector_store.adapter import CandidateIndex, VerifiedCandidate
from minimew.vector_store.manifest import CandidateManifest, VectorError, canonical


@dataclass(frozen=True)
class ScopeDocument:
    document_id: UUID
    version_id: UUID
    name: str
    version_number: int


@dataclass(frozen=True)
class RetrievalScope:
    user_id: UUID
    targets: tuple[CandidateManifest, ...]
    expires_at: datetime
    directory: tuple[ChunkBuild, ...] = ()
    namespace: str | None = None
    documents: tuple[ScopeDocument, ...] = ()
    embedding_credential_id: UUID | None = None


async def published_scope(
    sessions, user_id: UUID, *, thread_id: UUID | None = None
) -> RetrievalScope:
    async with sessions() as session, session.begin():
        project_id = None
        if thread_id is not None:
            thread = await session.scalar(
                select(Thread).where(Thread.id == thread_id, Thread.user_id == user_id)
            )
            if thread is None:
                raise PermissionError("Thread not owned")
            project_id = thread.project_id
        # 一个 SELECT/MVCC 快照同时读取 current、版本和产物，避免跨次查询混版。
        rows = (
            await session.execute(
                select(
                    DocumentVersion,
                    IndexArtifact,
                    Document.title,
                    KnowledgeBase.embedding_credential_id,
                )
                .select_from(Document)
                .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
                .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
                .join(IndexJob, IndexJob.version_id == DocumentVersion.id)
                .outerjoin(
                    IndexArtifact,
                    (IndexArtifact.job_id == IndexJob.id) & (IndexArtifact.status == "published"),
                )
                .where(
                    Document.user_id == user_id,
                    DocumentVersion.status == "published",
                    Document.deleted_at.is_(None),
                    KnowledgeBase.user_id == user_id,
                    KnowledgeBase.project_id == project_id,
                )
                .order_by(Document.id)
            )
        ).all()
        now = await session.scalar(select(func.clock_timestamp()))
        targets = []
        directory = []
        documents = []
        for version, artifact, title, _ in rows:
            if artifact is None:
                raise VectorError("INDEX_ARTIFACT_INVALID")
            manifest = read_artifact(artifact)
            if (
                manifest.scope.identity.user_id != user_id
                or manifest.scope.identity.version_id != version.id
            ):
                raise VectorError("INDEX_ARTIFACT_INVALID")
            documents.append(
                ScopeDocument(version.document_id, version.id, title, version.version_number)
            )
            targets.append(manifest)
            directory.append(TypeAdapter(ChunkBuild).validate_json(canonical(artifact.chunks)))
        # 严格短于旧 current 的一小时保留期，为已取得 scope 的请求留出完成窗口。
        return RetrievalScope(
            user_id,
            tuple(targets),
            now + timedelta(minutes=55),
            tuple(directory),
            "minimew_project_" + project_id.hex if project_id else None,
            tuple(documents),
            rows[0][3] if rows else None,
        )


async def search_published(
    sessions,
    index: CandidateIndex,
    scope: RetrievalScope,
    user_id,
    vector,
    profile,
    *,
    top_k=3,
    offload=asyncio.to_thread,
    lexical_query=None,
):
    if scope.user_id != user_id or any(t.scope.identity.user_id != user_id for t in scope.targets):
        raise PermissionError("Retrieval scope owner mismatch")
    async with sessions() as session:
        now = await session.scalar(select(func.clock_timestamp()))
    if now >= scope.expires_at:
        raise VectorError("INDEX_SCOPE_EXPIRED")
    deadline = time.monotonic() + (scope.expires_at - now).total_seconds()
    index = scoped_index(index, scope.namespace)
    # 评测使用独立副本，保留其冻结资料；聊天范围始终遵守文档下线。
    evaluation = str(getattr(index.backend, "namespace", "")).startswith("minimew_eval_")

    async def revoked():
        if evaluation:
            return set()
        async with sessions() as session:
            return set(
                (
                    await session.scalars(
                        select(Document.id).where(
                            Document.user_id == user_id,
                            Document.deleted_at.is_not(None),
                            Document.id.in_([m.scope.identity.document_id for m in scope.targets]),
                        )
                    )
                ).all()
            )

    excluded = await revoked()
    hits = []
    for manifest in scope.targets:
        if manifest.scope.identity.document_id in excluded:
            continue
        if time.monotonic() >= deadline:
            raise VectorError("INDEX_SCOPE_EXPIRED")
        if lexical_query is None:
            rows = await offload(
                index.search, VerifiedCandidate(manifest), vector, profile, top_k=top_k
            )
        else:
            rows = await offload(
                index.lexical, VerifiedCandidate(manifest), lexical_query, top_k=top_k
            )
        hits.extend(rows)
    excluded = await revoked()
    denied_versions = {
        str(m.scope.identity.version_id)
        for m in scope.targets
        if m.scope.identity.document_id in excluded
    }
    hits = [r for r in hits if str(r["payload"]["identity"]["version_id"]) not in denied_versions]
    return sorted(hits, key=lambda row: (-row["score"], row["chunk_id"]))[:top_k]


def scoped_index(index, namespace):
    if namespace is None or getattr(index.backend, "namespace", None) == namespace:
        return index
    return CandidateIndex(index.backend.for_namespace(namespace), index.batch_size)


async def validate_delivery(sessions, user_id, document_ids):
    """在工具交付点重验下线，覆盖耗时评判和Checkpoint恢复后的旧候选。"""
    if not document_ids:
        return
    async with sessions() as session:
        revoked = await session.scalar(
            select(Document.id)
            .where(
                Document.user_id == user_id,
                Document.id.in_(document_ids),
                Document.deleted_at.is_not(None),
            )
            .limit(1)
        )
    if revoked is not None:
        raise VectorError("INDEX_SCOPE_REVOKED")
