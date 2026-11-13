import hashlib
import json
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from test_vector_store import FakeBackend, candidate

from minimew.model_control import ModelSnapshot, ProfileSnapshot, snapshot_for_run
from minimew.models import Run
from minimew.rag.fusion import fuse
from minimew.rag.protocol import MaterialGroup, RagPolicy, RetrievalResult, RetrievalTrace
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import HYBRID_LAYOUT, VectorError, canonical


class HybridBackend(FakeBackend):
    def fetch(self, scope, ids):
        return [r for r in self.read(scope, 100) if r["chunk_id"] in ids]

    def ensure_collection(self, **kwargs):
        pass

    def upsert(self, rows, **kwargs):
        return super().upsert(rows)

    def lexical(self, scope, query, limit):
        return [
            {"entity": row, "distance": 1.5}
            for row in self.read(scope, 100)
            if query in row["retrieval_text"]
        ][:limit]


def hit(identity, score, version="v1"):
    return {
        "chunk_id": identity,
        "score": score,
        "payload": {"identity": {"version_id": version}, "build_fingerprint": "b"},
    }


def test_rrf_uses_rank_deduplicates_and_keeps_stage_score_semantics():
    policy = RagPolicy(strategy="hybrid")
    rows = fuse([hit("a", 0.9), hit("a", 0.9), hit("b", 0.7)], [hit("b", 42), hit("c", 3)], policy)
    assert [r.row["chunk_id"] for r in rows] == ["b", "a", "c"]
    assert rows[0].rrf == pytest.approx(1 / 62 + 1 / 61)
    result = RetrievalResult((MaterialGroup((rows[0],)),), RetrievalTrace("hybrid"))
    assert result.rows[0]["score_kind"] == "rrf"
    assert result.rows[0]["stage_scores"] == {"dense": 0.7, "bm25": 42, "rrf": rows[0].rrf}


def test_legacy_scope_identity_is_byte_compatible_and_layout_changes_identity():
    scope = candidate().manifest.scope
    old = asdict(scope)
    old.pop("index_layout")
    assert scope.scope_id == hashlib.sha256(canonical(old).encode()).hexdigest()
    assert replace(scope, index_layout=HYBRID_LAYOUT).scope_id != scope.scope_id


def test_capabilities_are_explicit_and_unknown_layout_is_not_dense_fallback():
    manifest = candidate().manifest
    assert CandidateIndex.capabilities(manifest) == frozenset({"dense"})
    assert CandidateIndex.capabilities(
        replace(manifest, scope=replace(manifest.scope, index_layout=HYBRID_LAYOUT))
    ) == frozenset({"dense", "bm25"})
    with pytest.raises(VectorError, match="VECTOR_BUILD_MISMATCH"):
        CandidateIndex.capabilities(
            replace(manifest, scope=replace(manifest.scope, index_layout="unknown"))
        )


def test_hybrid_validation_rejects_changed_lexical_text_and_old_capability():
    source = candidate()
    with pytest.raises(VectorError, match="VECTOR_CAPABILITY_UNSUPPORTED"):
        CandidateIndex(HybridBackend()).lexical(
            CandidateIndex(HybridBackend()).stage(source), "餐费"
        )
    scope = replace(source.manifest.scope, index_layout=HYBRID_LAYOUT)
    hybrid = replace(source, manifest=replace(source.manifest, scope=scope))
    # The fake candidate's row IDs remain valid expected IDs; the scope is independently checked.
    backend = HybridBackend()
    index = CandidateIndex(backend)
    index.stage(hybrid)
    next(iter(backend.rows.values()))["retrieval_text"] = "forged"
    with pytest.raises(VectorError, match="VECTOR_MANIFEST_MISMATCH"):
        index.verify(hybrid.manifest)


def test_legacy_snapshot_hash_is_not_changed_and_v2_freezes_policy(model_configuration):
    one = ModelSnapshot(
        answer=ProfileSnapshot(
            profile_id=uuid4(), profile_version=1, configuration=model_configuration
        )
    )
    old = one.model_dump(mode="json", exclude_none=True)
    digest = hashlib.sha256(
        json.dumps(old, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert snapshot_for_run(Run(model_snapshot_json=old, model_catalog_hash=digest)).rag is None
    new = ModelSnapshot(schema_version=2, answer=one.answer, rag=RagPolicy(strategy="hybrid"))
    assert new.catalog_hash() != digest and new.rag.reranker == "BAAI/bge-reranker-base"
