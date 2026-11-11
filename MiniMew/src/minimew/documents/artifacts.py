"""冻结产物编解码与退休入账；不从向量库重建预期清单。"""

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta

from pydantic import TypeAdapter
from sqlalchemy import func, select

from minimew.documents.chunking import ChunkBuild
from minimew.models import IndexArtifact, IndexCleanupJob
from minimew.vector_store.manifest import (
    COLLECTION,
    SPACE_HASH,
    CandidateManifest,
    VectorError,
    canonical,
)


def artifact_values(chunks: ChunkBuild, manifest: CandidateManifest) -> dict:
    value = {
        "manifest": json.loads(canonical(asdict(manifest))),
        "chunks": json.loads(canonical(asdict(chunks))),
        "target_collection": COLLECTION,
    }
    return {**value, "content_sha256": hashlib.sha256(canonical(value).encode()).hexdigest()}


def read_artifact(artifact: IndexArtifact) -> CandidateManifest:
    value = {key: getattr(artifact, key) for key in ("manifest", "chunks", "target_collection")}
    try:
        if hashlib.sha256(canonical(value).encode()).hexdigest() != artifact.content_sha256:
            raise ValueError("Artifact checksum mismatch")
        manifest = TypeAdapter(CandidateManifest).validate_json(canonical(artifact.manifest))
        chunks = TypeAdapter(ChunkBuild).validate_json(canonical(artifact.chunks))
        if (
            artifact.target_collection != COLLECTION
            or manifest.encoding_space != SPACE_HASH
            or manifest.scope.attempt_id != artifact.id
            or manifest.scope.identity != chunks.identity
            or manifest.scope.build_fingerprint != chunks.build_fingerprint
            or manifest.scope.source_sha256 != chunks.source_sha256
            or not manifest.entries
            or len({e.row_id for e in manifest.entries}) != len(manifest.entries)
            or tuple(e.chunk_id for e in manifest.entries)
            != tuple(str(c.chunk_id) for c in chunks.chunks)
        ):
            raise ValueError("Artifact identity mismatch")
        return manifest
    except (TypeError, ValueError, KeyError) as exc:
        raise VectorError("INDEX_ARTIFACT_INVALID") from exc


async def retire_artifacts(session, job_id, *, grace_seconds=0):
    rows = (
        await session.scalars(
            select(IndexArtifact)
            .where(IndexArtifact.job_id == job_id, IndexArtifact.status != "retired")
            .with_for_update()
        )
    ).all()
    now = await session.scalar(select(func.clock_timestamp()))
    for artifact in rows:
        artifact.status = "retired"
        session.add(
            IndexCleanupJob(artifact_id=artifact.id, due_at=now + timedelta(seconds=grace_seconds))
        )
