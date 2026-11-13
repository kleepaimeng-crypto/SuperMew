"""唯一候选写入/核验/检索接口；发布和执行资格由 Catalog/Worker 拥有。"""

import math
from dataclasses import dataclass
from typing import Protocol

from minimew.documents.profiles import EmbeddingProfile
from minimew.vector_store.manifest import (
    HYBRID_LAYOUT,
    PROFILE,
    SPACE_HASH,
    CandidateBuild,
    CandidateManifest,
    CandidateScope,
    VectorError,
    canonical,
    normalized_vector,
    record_hash,
)


class VectorBackend(Protocol):
    def ensure_collection(self) -> None: ...
    def read(self, scope: CandidateScope, max_rows: int) -> list[dict]: ...
    def upsert(self, rows: list[dict]) -> None: ...
    def search(self, scope: CandidateScope, vector: list[float], limit: int) -> list[dict]: ...
    def delete(self, scope: CandidateScope) -> None: ...
    def lexical(self, scope: CandidateScope, query: str, limit: int) -> list[dict]: ...
    def fetch(self, scope: CandidateScope, ids: list[str]) -> list[dict]: ...


@dataclass(frozen=True)
class VerifiedCandidate:
    # 只是核验回执，不是 published 状态或用户授权凭据。
    manifest: CandidateManifest


class CandidateIndex:
    def __init__(self, backend: VectorBackend, batch_size: int = 4):
        if not 1 <= batch_size <= 64:
            raise ValueError("Invalid batch size")
        self.backend = backend
        self.batch_size = batch_size

    @staticmethod
    def capabilities(manifest):
        if manifest.encoding_space != SPACE_HASH:
            raise VectorError("VECTOR_BUILD_MISMATCH")
        if manifest.scope.index_layout == HYBRID_LAYOUT:
            return frozenset({"dense", "bm25"})
        if manifest.scope.index_layout == "dense-cosine-v1":
            return frozenset({"dense"})
        raise VectorError("VECTOR_BUILD_MISMATCH")

    def _validate(
        self, manifest: CandidateManifest, rows: list[dict], *, complete: bool
    ) -> set[str]:
        if manifest.encoding_space != SPACE_HASH:
            raise VectorError("VECTOR_BUILD_MISMATCH")
        expected = {entry.row_id: entry for entry in manifest.entries}
        if not expected or len(expected) != len(manifest.entries):
            raise VectorError("VECTOR_MANIFEST_MISMATCH")
        seen = set()
        try:
            for row in rows:
                entry = expected[row["id"]]
                if row["id"] in seen or row["chunk_id"] != entry.chunk_id:
                    raise ValueError("Unexpected identity")
                if any(row[key] != value for key, value in manifest.scope.scalar_fields.items()):
                    raise ValueError("Unexpected scope")
                actual_hash = record_hash(
                    canonical(row["payload"]), normalized_vector(row["vector"])
                )
                if (
                    manifest.scope.index_layout == HYBRID_LAYOUT
                    and row.get("retrieval_text") != row["payload"]["embedding_text"]
                ):
                    raise ValueError("Unexpected lexical text")
                if (
                    row["record_sha256"] != entry.record_sha256
                    or actual_hash != entry.record_sha256
                ):
                    raise ValueError("Unexpected content")
                seen.add(row["id"])
        except (KeyError, TypeError, ValueError, VectorError) as exc:
            raise VectorError("VECTOR_MANIFEST_MISMATCH") from exc
        if complete and seen != set(expected):
            raise VectorError("VECTOR_MANIFEST_MISMATCH")
        return seen

    def stage(self, candidate: CandidateBuild) -> VerifiedCandidate:
        manifest = candidate.manifest
        entities = [record.entity(manifest.scope) for record in candidate.records]
        self._validate(manifest, entities, complete=True)
        hybrid = manifest.scope.index_layout == HYBRID_LAYOUT
        self.backend.ensure_collection(**({"layout": HYBRID_LAYOUT} if hybrid else {}))
        existing = self.backend.read(manifest.scope, len(manifest.entries) + 1)
        seen = self._validate(manifest, existing, complete=False)
        missing = [row for row in entities if row["id"] not in seen]
        for start in range(0, len(missing), self.batch_size):
            self.backend.upsert(
                missing[start : start + self.batch_size],
                **({"layout": HYBRID_LAYOUT} if hybrid else {}),
            )
        return self.verify(manifest)

    def verify(self, manifest: CandidateManifest) -> VerifiedCandidate:
        rows = self.backend.read(manifest.scope, len(manifest.entries) + 1)
        self._validate(manifest, rows, complete=True)
        return VerifiedCandidate(manifest)

    def search(
        self,
        verified: VerifiedCandidate,
        vector: list[float],
        query_profile: EmbeddingProfile,
        *,
        top_k: int = 3,
    ) -> list[dict]:
        if query_profile != PROFILE:
            raise VectorError("VECTOR_BUILD_MISMATCH")
        if not 1 <= top_k <= 20:
            raise VectorError("VECTOR_INVALID_QUERY")
        values = list(normalized_vector(vector))
        manifest = verified.manifest
        rows = self.backend.search(manifest.scope, values, top_k)
        return self._hits(manifest, rows, top_k)

    def lexical(self, verified, query, *, top_k=10):
        if verified.manifest.scope.index_layout != HYBRID_LAYOUT:
            raise VectorError("VECTOR_CAPABILITY_UNSUPPORTED")
        if not isinstance(query, str) or not query.strip() or not 1 <= top_k <= 20:
            raise VectorError("VECTOR_INVALID_QUERY")
        return self._hits(
            verified.manifest, self.backend.lexical(verified.manifest.scope, query, top_k), top_k
        )

    def fetch(self, manifest, ids):
        if not 1 <= len(ids) <= 6 or len(set(ids)) != len(ids):
            raise VectorError("VECTOR_INVALID_QUERY")
        rows = self.backend.fetch(manifest.scope, ids)
        self._validate(manifest, rows, complete=False)
        if {row["chunk_id"] for row in rows} != set(ids) or len(rows) != len(ids):
            raise VectorError("VECTOR_MANIFEST_MISMATCH")
        return [{"chunk_id": r["chunk_id"], "score": 0.0, "payload": r["payload"]} for r in rows]

    def _hits(self, manifest, rows, top_k):
        try:
            if len(rows) > top_k:
                raise VectorError("VECTOR_INVALID_RESULT")
            self._validate(manifest, [row["entity"] for row in rows], complete=False)
            hits = []
            for row in rows:
                score = float(row["distance"])
                if not math.isfinite(score):
                    raise VectorError("VECTOR_INVALID_RESULT")
                payload = row["entity"]["payload"]
                hits.append(
                    {"chunk_id": row["entity"]["chunk_id"], "score": score, "payload": payload}
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise VectorError("VECTOR_INVALID_RESULT") from exc
        return hits

    def delete_candidate(self, scope: CandidateScope) -> None:
        """调用方只传入已获准清理的候选范围；不接收任意 filter。"""
        self.backend.delete(scope)
        if self.backend.read(scope, 1):
            raise VectorError("VECTOR_DELETE_INCOMPLETE")
