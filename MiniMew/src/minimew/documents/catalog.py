"""目录预留与只读查询；HTTP 不运行 parser、Embedding 或索引发布。"""

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from minimew.documents.artifacts import retire_artifacts
from minimew.documents.profiles import BuildConfiguration, preset, read_build
from minimew.documents.schemas import (
    DocumentPage,
    DocumentResponse,
    IndexJobResponse,
    UploadReceipt,
    VersionResponse,
)
from minimew.documents.storage import Source, SourceStore
from minimew.models import Document, DocumentSubmission, DocumentVersion, IndexJob, KnowledgeBase


class DocumentNotFound(Exception):
    pass


class SubmissionConflict(Exception):
    pass


class BuildUnavailable(Exception):
    pass


async def source_lock(session: AsyncSession, version_id: UUID) -> None:
    # 同一个锁也由 orphan cleanup 使用，防止清理尚未提交的 source。
    key = int.from_bytes(hashlib.sha256(version_id.bytes).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def version_response(version: DocumentVersion) -> VersionResponse:
    try:
        build = read_build(version.build_configuration, version.build_fingerprint)
        if build.source_format != version.source_format:
            raise ValueError("Source parser format mismatch")
    except ValueError:
        raise BuildUnavailable from None
    return VersionResponse.model_validate({**version.__dict__, "build_configuration": build})


async def owned_document(session, user_id, document_id, *, lock=False):
    query = select(Document).where(Document.id == document_id, Document.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    document = await session.scalar(query)
    if document is None:
        raise DocumentNotFound
    return document


def document_query():
    # 同一语句快照同时读取指针与最近Job，失败清空pending后仍可恢复任务。
    return (
        select(Document, IndexJob)
        .outerjoin(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_number == Document.last_version_number),
        )
        .outerjoin(IndexJob, IndexJob.version_id == DocumentVersion.id)
        .execution_options(populate_existing=True)
    )


def document_response(document, job):
    if document.last_version_number > 0 and job is None:
        raise BuildUnavailable
    return DocumentResponse.model_validate(
        {**document.__dict__, "latest_job": IndexJobResponse.model_validate(job) if job else None}
    )


async def read_document(session, user_id, document_id):
    row = (
        await session.execute(
            document_query().where(Document.id == document_id, Document.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise DocumentNotFound
    return document_response(*row)


async def receipt(session, document, version_id, job_id):
    version = await session.get(DocumentVersion, version_id)
    job = await session.get(IndexJob, job_id)
    if (
        version is None
        or job is None
        or version.document_id != document.id
        or job.version_id != version.id
    ):
        raise BuildUnavailable
    return UploadReceipt(
        document=await read_document(session, document.user_id, document.id),
        version=version_response(version),
        job=IndexJobResponse.model_validate(job),
    )


async def reserve_document(
    session: AsyncSession,
    store: SourceStore,
    user_id: UUID,
    source: Source,
    key: str,
    default_profile: str,
    document_id: UUID | None = None,
    *,
    index_layout: str = "dense-cosine-v1",
    expected_current_version: UUID | None = None,
    project_id: UUID | None = None,
) -> tuple[UploadReceipt, bool]:
    request_hash = hashlib.sha256(
        json.dumps(
            [str(document_id) if document_id else None, source.filename, source.sha256],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    async with session.begin():
        initial = preset(default_profile).model_copy(
            update={"index_layout": index_layout, "parser": "markdown-txt-v2"}
        )
        initial = BuildConfiguration.model_validate(initial.model_dump())
        from minimew.projects import owned_project

        if document_id is not None:
            target = await owned_document(session, user_id, document_id)
            current_kb = await session.get(KnowledgeBase, target.knowledge_base_id)
            if project_id is not None and current_kb.project_id != project_id:
                raise DocumentNotFound
            project_id = current_kb.project_id
        if project_id is not None:
            await owned_project(session, user_id, project_id)
        await session.execute(
            insert(KnowledgeBase)
            .values(
                user_id=user_id,
                project_id=project_id,
                build_configuration=initial.model_dump(mode="json"),
                build_fingerprint=initial.fingerprint(),
            )
            .on_conflict_do_nothing(
                index_elements=[KnowledgeBase.user_id]
                if project_id is None
                else [KnowledgeBase.project_id],
                index_where=KnowledgeBase.project_id.is_(None)
                if project_id is None
                else KnowledgeBase.project_id.is_not(None),
            )
        )
        kb = await session.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id, KnowledgeBase.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        previous = await session.get(DocumentSubmission, (kb.id, key))
        if previous is not None:
            if previous.request_sha256 != request_hash:
                raise SubmissionConflict
            document = await owned_document(session, user_id, previous.document_id, lock=True)
            return await receipt(session, document, previous.version_id, previous.job_id), False
        if document_id is None:
            document = Document(user_id=user_id, knowledge_base_id=kb.id, title=source.filename)
            session.add(document)
            await session.flush()
        else:
            document = await owned_document(session, user_id, document_id, lock=True)
            if document.deleted_at is not None:
                raise SubmissionConflict
            if expected_current_version is not None and (
                document.current_version_id != expected_current_version
                or document.pending_version_id is not None
            ):
                raise SubmissionConflict
        try:
            build = read_build(kb.build_configuration, kb.build_fingerprint)
            build = BuildConfiguration.model_validate(
                {**build.model_dump(mode="json"), "source_format": source.format}
            )
        except ValueError:
            raise BuildUnavailable from None
        # 内容/构建相同只复用 current/pending；失败与被替代版本不复活。
        version = await session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id.in_(
                    [v for v in [document.pending_version_id, document.current_version_id] if v]
                ),
                DocumentVersion.document_id == document.id,
                DocumentVersion.content_sha256 == source.sha256,
                DocumentVersion.build_fingerprint == build.fingerprint(),
                DocumentVersion.status.in_(["candidate", "published"]),
            )
            .order_by(DocumentVersion.version_number.desc())
        )
        if version is not None:
            job = await session.scalar(select(IndexJob).where(IndexJob.version_id == version.id))
            created = False
        else:
            version_id = uuid4()
            await source_lock(session, version_id)
            # 完整文件先原子落盘；后续 commit 不确定时保留，交给带 DB 锁的精确清理。
            await run_in_threadpool(store.write, version_id, source)
            if document.pending_version_id is not None:
                old = await session.get(DocumentVersion, document.pending_version_id)
                old_job = await session.scalar(
                    select(IndexJob).where(IndexJob.version_id == old.id).with_for_update()
                )
                old.status = "superseded"
                old_job.status = "cancelled"
                old_job.finished_at = await session.scalar(select(text("clock_timestamp()")))
                await retire_artifacts(session, old_job.id)
                await session.flush()
            document.last_version_number += 1
            document.publication_fence += 1
            version = DocumentVersion(
                id=version_id,
                document_id=document.id,
                user_id=user_id,
                knowledge_base_id=kb.id,
                version_number=document.last_version_number,
                publication_fence=document.publication_fence,
                source_filename=source.filename,
                source_format=source.format,
                source_size=len(source.content),
                content_sha256=source.sha256,
                build_configuration=build.model_dump(mode="json"),
                build_fingerprint=build.fingerprint(),
            )
            session.add(version)
            await session.flush()
            job = IndexJob(
                version_id=version.id,
                document_id=document.id,
                user_id=user_id,
                knowledge_base_id=kb.id,
            )
            session.add(job)
            document.pending_version_id = version.id
            await session.flush()
            created = True
        if job is None:
            raise BuildUnavailable
        session.add(
            DocumentSubmission(
                knowledge_base_id=kb.id,
                user_id=user_id,
                idempotency_key=key,
                request_sha256=request_hash,
                document_id=document.id,
                version_id=version.id,
                job_id=job.id,
            )
        )
        await session.flush()
        result = await receipt(session, document, version.id, job.id)
    return result, created


async def list_documents(
    session, user_id, limit, offset, *, project_id=None, archived=False
) -> DocumentPage:
    async with session.begin():
        if project_id is not None:
            from minimew.projects import owned_project

            await owned_project(session, user_id, project_id)
        rows = (
            await session.execute(
                document_query()
                .where(
                    Document.user_id == user_id,
                    Document.knowledge_base_id.in_(
                        select(KnowledgeBase.id).where(
                            KnowledgeBase.user_id == user_id, KnowledgeBase.project_id == project_id
                        )
                    ),
                    Document.deleted_at.is_not(None) if archived else Document.deleted_at.is_(None),
                )
                .order_by(Document.created_at.desc(), Document.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return DocumentPage(
            items=[document_response(*row) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )


async def get_document(session, user_id, document_id) -> DocumentResponse:
    async with session.begin():
        return await read_document(session, user_id, document_id)


async def get_version(session, user_id, document_id, version_id) -> VersionResponse:
    async with session.begin():
        await owned_document(session, user_id, document_id)
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.user_id == user_id,
            )
        )
        if version is None:
            raise DocumentNotFound
        return version_response(version)


async def get_job(session, user_id, job_id) -> IndexJobResponse:
    async with session.begin():
        job = await session.scalar(
            select(IndexJob).where(IndexJob.id == job_id, IndexJob.user_id == user_id)
        )
        if job is None:
            raise DocumentNotFound
        return IndexJobResponse.model_validate(job)


async def withdraw(session, user_id, document_id):
    from sqlalchemy import func

    async with session.begin():
        document = await owned_document(session, user_id, document_id, lock=True)
        if document.deleted_at is not None:
            return await read_document(session, user_id, document_id)
        now = await session.scalar(select(func.clock_timestamp()))
        document.deleted_at = now
        document.publication_fence += 1
        document.pending_version_id = None
        jobs = (
            await session.scalars(
                select(IndexJob)
                .where(IndexJob.document_id == document.id)
                .order_by(IndexJob.id)
                .with_for_update()
            )
        ).all()
        for job in jobs:
            if job.status in {"pending", "running", "staged"}:
                job.status, job.finished_at = "cancelled", now
                version = await session.get(DocumentVersion, job.version_id)
                if version.status == "candidate":
                    version.status = "superseded"
            await retire_artifacts(session, job.id, grace_seconds=3600)
        await session.flush()
        return await read_document(session, user_id, document_id)
