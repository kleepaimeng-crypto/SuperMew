"""显式固定真实文档版本并准备评测专属索引；不改变用户发布目录。"""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, select

from minimew.documents.artifacts import read_artifact
from minimew.documents.chunking import ChunkBuild
from minimew.documents.parsing import parse_source
from minimew.documents.profiles import BAILIAN_LAYOUTS
from minimew.documents.retrieval import RetrievalScope
from minimew.evaluation.runtime_protocol import CorpusV2 as Corpus
from minimew.evaluation.runtime_protocol import SourceTextV2 as SourceText
from minimew.evaluation.store import EvaluationConflict, EvaluationNotFound
from minimew.models import (
    Document,
    DocumentVersion,
    EvaluationCorpus,
    EvaluationJob,
    IndexArtifact,
    IndexJob,
    KnowledgeBase,
)
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import canonical
from minimew.vector_store.milvus import MilvusBackend


async def prepare(sessions, user_id, dataset, mapping, source_store, vector_settings):
    if set(mapping) != {s.source_id for s in dataset.sources} or len(set(mapping.values())) != len(
        mapping
    ):
        raise EvaluationConflict
    corpus_id = uuid4()
    namespace = "minimew_eval_" + corpus_id.hex
    originals, directory, targets, texts, bindings = [], [], [], [], []
    async with sessions() as session:
        for source_id, version_id in sorted(mapping.items()):
            row = (
                await session.execute(
                    select(DocumentVersion, IndexArtifact)
                    .join(Document, Document.current_version_id == DocumentVersion.id)
                    .join(IndexJob, IndexJob.version_id == DocumentVersion.id)
                    .join(
                        IndexArtifact,
                        (IndexArtifact.job_id == IndexJob.id)
                        & (IndexArtifact.status == "published"),
                    )
                    .where(
                        DocumentVersion.id == version_id,
                        Document.user_id == user_id,
                        Document.deleted_at.is_(None),
                    )
                )
            ).one_or_none()
            if row is None:
                raise EvaluationNotFound
            version, artifact = row
            manifest = read_artifact(artifact)
            if manifest.scope.index_layout in BAILIAN_LAYOUTS:
                raise EvaluationConflict(
                    "API embedding corpora require an evaluation model binding"
                )
            targets.append(manifest)
            directory.append(TypeAdapter(ChunkBuild).validate_json(canonical(artifact.chunks)))
            originals.append((source_id, version.id, version.source_format, version.content_sha256))
            bindings.append({"source_id": source_id, "version_id": str(version.id)})
    for source_id, version_id, source_format, digest in originals:
        content = await asyncio.to_thread(source_store.read, version_id, digest)
        parsed = await asyncio.to_thread(parse_source, content, source_format, digest)
        texts.append(SourceText(source_id=source_id, text=parsed.text))
    corpus = Corpus(sources=tuple(texts))
    if sorted([s.identity() for s in corpus.sources], key=lambda s: s.source_id) != sorted(
        dataset.sources, key=lambda s: s.source_id
    ):
        raise EvaluationConflict
    async with sessions() as session, session.begin():
        now = await session.scalar(select(func.clock_timestamp()))
        scope = RetrievalScope(user_id, tuple(targets), now + timedelta(days=7), tuple(directory))
        payload = {
            "ready": False,
            "corpus": corpus.model_dump(mode="json"),
            "corpus_fingerprint": dataset.corpus_fingerprint(),
            "scope": json.loads(
                TypeAdapter(RetrievalScope).dump_json(scope, exclude={"embedding_credential_id"})
            ),
            "bindings": bindings,
        }
        session.add(
            EvaluationCorpus(id=corpus_id, user_id=user_id, collection=namespace, payload=payload)
        )
    source = target = None
    try:
        source = await asyncio.to_thread(MilvusBackend.connect, vector_settings)
        target = await asyncio.to_thread(
            MilvusBackend.connect, vector_settings, namespace=namespace
        )
        target_index = CandidateIndex(target)
        for manifest in targets:
            async with sessions() as session:
                kb = await session.get(KnowledgeBase, manifest.scope.identity.knowledge_base_id)
                if kb is None or kb.user_id != user_id:
                    raise EvaluationNotFound
                scoped_source = (
                    source.for_namespace("minimew_project_" + kb.project_id.hex)
                    if kb.project_id
                    else source
                )
            await asyncio.to_thread(CandidateIndex(scoped_source).copy_to, manifest, target_index)
        async with sessions() as session, session.begin():
            saved = await session.get(EvaluationCorpus, corpus_id, with_for_update=True)
            saved.payload = {**saved.payload, "ready": True}
        return corpus_id
    finally:
        for backend in (source, target):
            if backend is not None:
                await asyncio.to_thread(backend.close)


async def cleanup(sessions, user_id, corpus_id, settings):
    async with sessions() as session, session.begin():
        corpus = await session.scalar(
            select(EvaluationCorpus)
            .where(EvaluationCorpus.id == corpus_id, EvaluationCorpus.user_id == user_id)
            .with_for_update()
        )
        if corpus is None:
            raise EvaluationNotFound
        # 报告仍引用产物时也不隐式销毁；需先有显式归档流程。
        if await session.scalar(
            select(EvaluationJob.id).where(EvaluationJob.corpus_id == corpus_id).limit(1)
        ):
            raise EvaluationConflict
        backend = await asyncio.to_thread(
            MilvusBackend.connect, settings, namespace=corpus.collection
        )
        try:
            for layout in {t["scope"]["index_layout"] for t in corpus.payload["scope"]["targets"]}:
                name = backend.collection(layout)
                if await asyncio.to_thread(
                    backend._rpc, backend.client.has_collection, collection_name=name
                ):
                    await asyncio.to_thread(
                        backend._rpc, backend.client.drop_collection, collection_name=name
                    )
                if await asyncio.to_thread(
                    backend._rpc, backend.client.has_collection, collection_name=name
                ):
                    raise EvaluationConflict
            await session.delete(corpus)
        finally:
            await asyncio.to_thread(backend.close)
