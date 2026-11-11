"""写前冻结的预期产物。向量库读回的数据不能反过来定义预期。"""

import hashlib
import json
import math
import struct
from dataclasses import asdict, dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from minimew.documents.chunking import BuildIdentity, ChunkBuild
from minimew.documents.profiles import BuildConfiguration, preset


class VectorError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


PROFILE = preset("bge-small-zh-v1.5").embedding
SPACE_HASH = hashlib.sha256(canonical(PROFILE.model_dump(mode="json")).encode()).hexdigest()
COLLECTION = "minimew_dense_v1_" + SPACE_HASH[:16]


def normalized_vector(vector) -> tuple[float, ...]:
    try:
        values = tuple(struct.unpack("<512f", struct.pack("<512f", *vector)))
    except (ValueError, TypeError, struct.error, OverflowError) as exc:
        raise VectorError("VECTOR_INVALID_VECTOR") from exc
    if not all(math.isfinite(v) for v in values) or not math.isclose(
        math.sqrt(sum(v * v for v in values)), 1, abs_tol=1e-4
    ):
        raise VectorError("VECTOR_INVALID_VECTOR")
    return values


def record_hash(payload: str, vector: tuple[float, ...]) -> str:
    return hashlib.sha256(payload.encode() + b"\0" + struct.pack("<512f", *vector)).hexdigest()


@dataclass(frozen=True)
class CandidateScope:
    identity: BuildIdentity
    build_fingerprint: str
    source_sha256: str
    attempt_id: UUID

    @property
    def scope_id(self) -> str:
        return hashlib.sha256(canonical(asdict(self)).encode()).hexdigest()

    @property
    def scalar_fields(self) -> dict:
        return {
            "scope_id": self.scope_id,
            **{key: str(value) for key, value in asdict(self.identity).items()},
            "build_fingerprint": self.build_fingerprint,
        }


@dataclass(frozen=True)
class VectorRecord:
    row_id: str
    chunk_id: str
    payload_json: str
    vector: tuple[float, ...]
    record_sha256: str

    def entity(self, scope: CandidateScope) -> dict:
        return {
            "id": self.row_id,
            "chunk_id": self.chunk_id,
            **scope.scalar_fields,
            "payload": json.loads(self.payload_json),
            "vector": list(self.vector),
            "record_sha256": self.record_sha256,
        }


@dataclass(frozen=True)
class ManifestEntry:
    row_id: str
    chunk_id: str
    record_sha256: str


@dataclass(frozen=True)
class CandidateManifest:
    scope: CandidateScope
    entries: tuple[ManifestEntry, ...]
    encoding_space: str = SPACE_HASH


@dataclass(frozen=True)
class CandidateBuild:
    manifest: CandidateManifest
    records: tuple[VectorRecord, ...]


def make_candidate(
    chunks: ChunkBuild,
    build: BuildConfiguration,
    vectors: list[list[float]],
    attempt_id: UUID,
) -> CandidateBuild:
    if build.embedding != PROFILE or build.fingerprint() != chunks.build_fingerprint:
        raise VectorError("VECTOR_BUILD_MISMATCH")
    if not chunks.chunks or len(vectors) != len(chunks.chunks):
        raise VectorError("VECTOR_INVALID_VECTOR")
    scope = CandidateScope(
        chunks.identity, chunks.build_fingerprint, chunks.source_sha256, attempt_id
    )
    sections = {s.section_id: s for s in chunks.sections}
    rows = []
    for chunk, vector in zip(chunks.chunks, vectors):
        if (
            chunk.identity != chunks.identity
            or chunk.build_fingerprint != chunks.build_fingerprint
            or chunk.source_sha256 != chunks.source_sha256
            or hashlib.sha256(chunk.source_text.encode()).hexdigest() != chunk.content_sha256
            or hashlib.sha256(chunk.embedding_text.encode()).hexdigest() != chunk.embedding_sha256
            or chunk.section_id not in sections
        ):
            raise VectorError("VECTOR_BUILD_MISMATCH")
        values = normalized_vector(vector)
        payload = canonical({**asdict(chunk), "section": asdict(sections[chunk.section_id])})
        if len(payload.encode()) > 65535:
            raise VectorError("VECTOR_PAYLOAD_TOO_LARGE")
        row_id = str(uuid5(NAMESPACE_URL, scope.scope_id + ":" + str(chunk.chunk_id)))
        rows.append(
            VectorRecord(row_id, str(chunk.chunk_id), payload, values, record_hash(payload, values))
        )
    if len({r.row_id for r in rows}) != len(rows):
        raise VectorError("VECTOR_BUILD_MISMATCH")
    manifest = CandidateManifest(
        scope, tuple(ManifestEntry(r.row_id, r.chunk_id, r.record_sha256) for r in rows)
    )
    return CandidateBuild(manifest, tuple(rows))
