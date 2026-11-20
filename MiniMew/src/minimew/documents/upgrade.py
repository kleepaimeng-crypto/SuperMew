"""显式 Hybrid 构建升级：默认只读计划，--apply 按原上传/发布链创建候选。"""

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.catalog import require_no_embedding_rebuild, reserve_document
from minimew.documents.profiles import BAILIAN_ID, hybrid_preset, read_build
from minimew.documents.storage import Source, SourceStore
from minimew.models import Document, DocumentVersion, KnowledgeBase
from minimew.settings import Settings


async def upgrade(sessions, store, user_id, *, apply=False, parser=None, document_id=None):
    async with sessions() as session, session.begin():
        kb = await session.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id, KnowledgeBase.project_id.is_(None))
            .with_for_update()
        )
        if kb is None:
            raise ValueError("Knowledge base not found")
        build = read_build(kb.build_configuration, kb.build_fingerprint)
        if apply:
            await require_no_embedding_rebuild(session, kb.id)
        if build.embedding.id == BAILIAN_ID and apply:
            raise ValueError("Bailian layout upgrades use the embedding rebuild workflow")
        updates = (
            {"index_layout": hybrid_preset(build.embedding.id).index_layout}
            if parser is None
            else {"parser": parser}
        )
        target = type(build).model_validate({**build.model_dump(mode="json"), **updates})
        rows = (
            await session.execute(
                select(Document, DocumentVersion)
                .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
                .where(Document.knowledge_base_id == kb.id, Document.deleted_at.is_(None))
                .where(*([Document.id == document_id] if document_id is not None else []))
                .order_by(Document.id)
            )
        ).all()
        if document_id is not None and not rows:
            raise ValueError("Published document not found for this owner")
        plans = []
        for doc, version in rows:
            old = read_build(version.build_configuration, version.build_fingerprint)
            expected = target.model_copy(update={"source_format": version.source_format})
            if old.fingerprint() == expected.fingerprint():
                continue
            if doc.pending_version_id is not None:
                pending = await session.get(DocumentVersion, doc.pending_version_id)
                if (
                    pending is not None
                    and pending.content_sha256 == version.content_sha256
                    and read_build(
                        pending.build_configuration, pending.build_fingerprint
                    ).fingerprint()
                    == expected.fingerprint()
                ):
                    continue
                raise ValueError("Pending upload exists; finish it before upgrading")
            content = await asyncio.to_thread(store.read, version.id, version.content_sha256)
            plans.append(
                (
                    doc.id,
                    version.id,
                    Source(
                        version.source_filename,
                        version.source_format,
                        content,
                        version.content_sha256,
                    ),
                )
            )
        if apply:
            kb.build_configuration = target.model_dump(mode="json")
            kb.build_fingerprint = target.fingerprint()
    receipts = []
    if apply:
        for doc_id, old_version, source in plans:
            # Deterministic intent and locked precondition prevent replacing a concurrent upload.
            async with sessions() as session:
                result, created = await reserve_document(
                    session,
                    store,
                    user_id,
                    source,
                    (
                        f"hybrid-v1-{old_version}"
                        if parser is None
                        else f"parser-v2-{old_version}-{target.fingerprint()}"
                    ),
                    build.embedding.id,
                    doc_id,
                    expected_current_version=old_version,
                )
                receipts.append(
                    {
                        "document_id": str(doc_id),
                        "version_id": str(result.version.id),
                        "job_id": str(result.job.id),
                        "created": created,
                    }
                )
    return {
        "apply": apply,
        "layout": target.index_layout,
        "parser": target.parser,
        "documents": len(plans),
        "jobs": receipts,
    }


async def main(args):
    settings = Settings()
    engine = create_engine(settings)
    try:
        result = await upgrade(
            async_sessionmaker(engine, expire_on_commit=False),
            SourceStore(settings.document_upload_dir),
            args.user,
            apply=args.apply,
            parser=args.parser,
            document_id=args.document,
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=UUID, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--parser", choices=["markdown-txt-v2"], help="仅升级解析器，保留索引布局")
    parser.add_argument("--document", type=UUID, help="仅为指定用户的一份已发布文档重建")
    asyncio.run(main(parser.parse_args()))
