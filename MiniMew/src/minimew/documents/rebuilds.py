"""Reserve per-document jobs and publish their verified candidates as one knowledge base."""

from uuid import uuid4

from sqlalchemy import func, select

from minimew.account_models.credentials import encrypt
from minimew.documents.artifacts import retire_artifacts
from minimew.documents.catalog import ensure_base
from minimew.documents.jobs import publish_candidate
from minimew.documents.profiles import BAILIAN_ID, HYBRID_LAYOUTS, hybrid_preset, preset, read_build
from minimew.documents.schemas import EmbeddingRebuildView, EmbeddingSettingsView
from minimew.embeddings.bailian import BASE_URL
from minimew.models import (
    Document,
    DocumentVersion,
    EmbeddingRebuild,
    IndexArtifact,
    IndexJob,
    KnowledgeBase,
    ModelCredential,
)
from minimew.projects import owned_project
from minimew.vector_store.manifest import PROFILE


class RebuildConflict(Exception):
    pass


def rebuild_history(kb):
    return (
        select(EmbeddingRebuild)
        .where(EmbeddingRebuild.knowledge_base_id == kb.id)
        .order_by(EmbeddingRebuild.created_at.desc(), EmbeddingRebuild.id.desc())
    )


async def saved_credential(session, kb):
    if kb is None:
        return None
    return await session.scalar(
        rebuild_history(kb)
        .with_only_columns(EmbeddingRebuild.credential_id)
        .where(EmbeddingRebuild.credential_id.is_not(None))
        .limit(1)
    )


async def settings_view(session, user_id, project_id, settings):
    async with session.begin():
        if project_id is not None:
            await owned_project(session, user_id, project_id)
        kb = await session.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id, KnowledgeBase.project_id == project_id)
            .with_for_update(read=True)
        )
        return await _view(session, kb, settings)


async def _view(session, kb, settings):
    build = (
        read_build(kb.build_configuration, kb.build_fingerprint)
        if kb
        else preset(settings.embedding_profile)
    )
    latest = await session.scalar(rebuild_history(kb).limit(1)) if kb else None
    rebuild = None
    if latest:
        total, completed = (
            await session.execute(
                select(
                    func.count(IndexJob.id),
                    func.count(IndexJob.id).filter(
                        (IndexJob.stage == "ready") & IndexJob.status.in_(["staged", "succeeded"])
                    ),
                ).where(IndexJob.rebuild_id == latest.id)
            )
        ).one()
        rebuild = EmbeddingRebuildView(
            id=latest.id,
            status=latest.status,
            total=total,
            completed=completed,
            target_model=read_build(latest.configuration, latest.fingerprint).embedding.model_id,
            error_code=latest.error_code,
        )
    return EmbeddingSettingsView(
        current_source="bailian" if build.embedding.id == BAILIAN_ID else "local",
        current_model=build.embedding.model_id,
        dimensions=build.embedding.dimension,
        bailian_key_configured=(await saved_credential(session, kb)) is not None,
        rebuild=rebuild,
    )


async def start_rebuild(session, user_id, project_id, change, settings):
    async with session.begin():
        kb = await ensure_base(
            session, user_id, project_id, settings.embedding_profile, settings.document_index_layout
        )
        previous = await session.scalar(rebuild_history(kb).limit(1))
        current = read_build(kb.build_configuration, kb.build_fingerprint)
        target_id = BAILIAN_ID if change.source == "bailian" else PROFILE.id
        if previous and previous.status == "building":
            if read_build(previous.configuration, previous.fingerprint).embedding.id == target_id:
                return await _view(session, kb, settings)
            raise RebuildConflict("EMBEDDING_REBUILD_IN_PROGRESS")
        if current.embedding.id == target_id and change.api_key is None:
            return await _view(session, kb, settings)

        docs = await _locked_documents(session, kb.id)
        if any(doc.pending_version_id for doc in docs):
            raise RebuildConflict("EMBEDDING_PENDING_UPLOAD")
        identity = uuid4()
        credential_id = None
        if change.source == "bailian":
            if change.api_key is not None:
                credential_id = uuid4()
                session.add(
                    ModelCredential(
                        id=credential_id,
                        user_id=user_id,
                        model_id=kb.id,
                        encrypted_value=encrypt(
                            settings,
                            user_id,
                            credential_id,
                            BASE_URL,
                            change.api_key.get_secret_value(),
                        ),
                    )
                )
                await session.flush()
            else:
                credential_id = await saved_credential(session, kb)
            if credential_id is None:
                raise RebuildConflict("EMBEDDING_KEY_REQUIRED")
        target = (hybrid_preset if current.index_layout in HYBRID_LAYOUTS else preset)(
            target_id
        ).model_copy(update={"parser": current.parser, "chunker": "structure-v2"})
        session.add(
            EmbeddingRebuild(
                id=identity,
                knowledge_base_id=kb.id,
                user_id=user_id,
                credential_id=credential_id,
                configuration=target.model_dump(mode="json"),
                fingerprint=target.fingerprint(),
                created_at=func.clock_timestamp(),
            )
        )
        await session.flush()
        sources = {
            row.id: row
            for row in await session.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.id.in_([doc.current_version_id for doc in docs])
                )
            )
        }
        jobs = []
        for doc in docs:
            if doc.current_version_id is None:
                continue
            source = sources[doc.current_version_id]
            build = target.model_copy(update={"source_format": source.source_format})
            doc.last_version_number += 1
            doc.publication_fence += 1
            version = DocumentVersion(
                id=uuid4(),
                document_id=doc.id,
                knowledge_base_id=kb.id,
                user_id=user_id,
                version_number=doc.last_version_number,
                publication_fence=doc.publication_fence,
                source_filename=source.source_filename,
                source_format=source.source_format,
                source_size=source.source_size,
                content_sha256=source.content_sha256,
                build_configuration=build.model_dump(mode="json"),
                build_fingerprint=build.fingerprint(),
            )
            session.add(version)
            doc.pending_version_id = version.id
            jobs.append(
                IndexJob(
                    version_id=version.id,
                    document_id=doc.id,
                    knowledge_base_id=kb.id,
                    user_id=user_id,
                    rebuild_id=identity,
                    source_version_id=source.id,
                )
            )
        await session.flush()
        session.add_all(jobs)
        await session.flush()
        return await _view(session, kb, settings)


async def _locked_documents(session, kb_id):
    return list(
        await session.scalars(
            select(Document)
            .where(Document.knowledge_base_id == kb_id, Document.deleted_at.is_(None))
            .order_by(Document.id)
            .with_for_update()
        )
    )


async def finalize_ready(sessions):
    pending = (
        select(IndexJob.id)
        .where(
            IndexJob.rebuild_id == EmbeddingRebuild.id,
            IndexJob.status.in_(["pending", "running", "staged"]),
            IndexJob.stage != "ready",
        )
        .exists()
    )
    failed = (
        select(IndexJob.id)
        .where(
            IndexJob.rebuild_id == EmbeddingRebuild.id,
            IndexJob.status.in_(["failed", "dead_letter"]),
        )
        .exists()
    )
    async with sessions() as session, session.begin():
        kb = await session.scalar(
            select(KnowledgeBase)
            .join(EmbeddingRebuild, EmbeddingRebuild.knowledge_base_id == KnowledgeBase.id)
            .where(EmbeddingRebuild.status == "building", (~pending | failed))
            .order_by(EmbeddingRebuild.created_at, EmbeddingRebuild.id)
            .limit(1)
            .with_for_update(of=KnowledgeBase, skip_locked=True)
        )
        if kb is None:
            return False
        rebuild = await session.scalar(
            rebuild_history(kb).where(EmbeddingRebuild.status == "building").limit(1)
        )
        if rebuild is None:
            return False
        docs = {doc.id: doc for doc in await _locked_documents(session, kb.id)}
        jobs = list(
            await session.scalars(
                select(IndexJob)
                .where(IndexJob.rebuild_id == rebuild.id)
                .order_by(IndexJob.document_id)
                .with_for_update()
            )
        )
        failed_job = next((job for job in jobs if job.status in {"failed", "dead_letter"}), None)
        if failed_job is None and any(
            job.stage != "ready" and job.status != "cancelled" for job in jobs
        ):
            return False
        ready = [job for job in jobs if job.status == "staged" and job.stage == "ready"]
        expected = {doc.id for doc in docs.values() if doc.current_version_id is not None}
        consistent = expected == {job.document_id for job in ready} and all(
            docs[job.document_id].current_version_id == job.source_version_id
            and docs[job.document_id].pending_version_id == job.version_id
            for job in ready
        )
        artifacts = {
            row.job_id: row
            for row in await session.scalars(
                select(IndexArtifact).where(
                    IndexArtifact.job_id.in_([job.id for job in ready]),
                    IndexArtifact.status == "staged",
                )
            )
        }
        artifacts_present = len(artifacts) == len(ready)
        now = await session.scalar(select(func.clock_timestamp()))
        if failed_job is not None or not consistent or not artifacts_present:
            for job in jobs:
                if job.status not in {"pending", "running", "staged"}:
                    continue
                doc = docs.get(job.document_id)
                if doc is not None and doc.pending_version_id == job.version_id:
                    doc.pending_version_id = None
                version = await session.get(DocumentVersion, job.version_id)
                version.status = "superseded"
                job.status, job.finished_at = "cancelled", now
                await retire_artifacts(session, job.id)
            rebuild.status = "failed"
            if failed_job:
                rebuild.error_code = failed_job.last_error_code or failed_job.error_code
            else:
                rebuild.error_code = (
                    "EMBEDDING_SET_CHANGED" if artifacts_present else "INDEX_ARTIFACT_INVALID"
                )
            return True
        for job in ready:
            await publish_candidate(session, docs[job.document_id], job, artifacts[job.id], now)
        kb.build_configuration, kb.build_fingerprint = rebuild.configuration, rebuild.fingerprint
        kb.embedding_credential_id = rebuild.credential_id
        rebuild.status = "succeeded"
        return True
