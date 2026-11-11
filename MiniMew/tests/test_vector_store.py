"""候选索引离线验证；假 backend 无网络，SDK 重试策略也用假 RPC 验证。"""

import copy
import hashlib
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_document_chunking import IDENTITY, CharacterTokenizer, configuration, parse

from minimew.documents.chunking import build_chunks
from minimew.documents.profiles import preset
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import (
    COLLECTION,
    PROFILE,
    VectorError,
    make_candidate,
    normalized_vector,
)
from minimew.vector_store.milvus import MilvusBackend, scope_filter


class FakeBackend:
    def __init__(self):
        self.rows = {}
        self.writes = 0
        self.fail_on = None
        self.committed_but_lost = False
        self.search_rows = None

    def ensure_collection(self):
        pass

    def read(self, scope, max_rows):
        return copy.deepcopy(
            [
                row
                for row in self.rows.values()
                if all(row.get(k) == v for k, v in scope.scalar_fields.items())
            ][:max_rows]
        )

    def upsert(self, rows):
        self.writes += 1
        if self.writes == self.fail_on:
            raise VectorError("VECTOR_UNAVAILABLE")
        self.rows.update({row["id"]: copy.deepcopy(row) for row in rows})
        if self.committed_but_lost:
            self.committed_but_lost = False
            raise VectorError("VECTOR_UNAVAILABLE")

    def search(self, scope, vector, limit):
        if self.search_rows is not None:
            return self.search_rows
        rows = self.read(scope, 100)
        return [
            {"entity": row, "distance": sum(a * b for a, b in zip(vector, row["vector"]))}
            for row in rows[:limit]
        ]

    def delete(self, scope):
        ids = [row["id"] for row in self.read(scope, 100)]
        for key in ids:
            del self.rows[key]


def candidate(attempt=None, identity=IDENTITY):
    build = configuration(size=384, overlap=48)
    chunks = build_chunks(
        parse("# 制度\n## 餐费\n每日150元。\n## 住宿\n住宿350元。\n## 材料\n申请需要票据。"),
        identity,
        build,
        CharacterTokenizer(),
    )
    vectors = [[float(i == j) for j in range(512)] for i in range(len(chunks.chunks))]
    return make_candidate(chunks, build, vectors, attempt or uuid4())


def test_manifest_is_frozen_before_any_write_and_uses_attempt_scoped_storage_ids():
    attempt = uuid4()
    one, same, other = candidate(attempt), candidate(attempt), candidate()
    assert one == same
    assert len(one.manifest.entries) == 3
    assert {r.chunk_id for r in one.records} == {r.chunk_id for r in other.records}
    assert {r.row_id for r in one.records}.isdisjoint(r.row_id for r in other.records)
    with pytest.raises(FrozenInstanceError):
        one.manifest.entries = ()


def test_partial_batch_failure_preserves_expected_manifest_and_resumes_missing_only():
    backend = FakeBackend()
    backend.fail_on = 2
    target = candidate()
    index = CandidateIndex(backend, batch_size=2)
    with pytest.raises(VectorError, match="VECTOR_UNAVAILABLE"):
        index.stage(target)
    assert len(backend.rows) == 2
    assert len(target.manifest.entries) == 3
    assert backend.writes == 2
    with pytest.raises(VectorError, match="VECTOR_MANIFEST_MISMATCH"):
        index.verify(target.manifest)
    backend.fail_on = None
    verified = index.stage(target)
    assert len(backend.rows) == 3 and backend.writes == 3
    assert index.stage(target) == verified
    assert backend.writes == 3


def test_committed_but_response_lost_does_not_duplicate_or_rewrite():
    backend = FakeBackend()
    backend.committed_but_lost = True
    target = candidate()
    index = CandidateIndex(backend)
    with pytest.raises(VectorError, match="VECTOR_UNAVAILABLE"):
        index.stage(target)
    assert len(backend.rows) == 3
    index.stage(target)
    assert backend.writes == 1


@pytest.mark.parametrize(
    "change", ["missing", "extra", "same_count_wrong_id", "payload", "vector", "version"]
)
def test_exact_manifest_verification_rejects_wrong_set_content_vector_or_scope(change):
    target = candidate()
    backend = FakeBackend()
    index = CandidateIndex(backend)
    index.stage(target)
    key = target.records[0].row_id
    row = backend.rows[key]
    if change == "missing":
        del backend.rows[key]
    elif change == "extra":
        backend.rows["extra"] = {**row, "id": "extra"}
    elif change == "same_count_wrong_id":
        backend.rows["wrong"] = {**backend.rows.pop(key), "id": "wrong"}
    elif change == "payload":
        row["payload"]["source_text"] = "每日300元"
    elif change == "vector":
        row["vector"] = [0.0, 1.0] + [0.0] * 510
    else:
        row["version_id"] = str(uuid4())
    with pytest.raises(VectorError, match="VECTOR_MANIFEST_MISMATCH"):
        index.verify(target.manifest)


def test_stage_does_not_silently_overwrite_conflicting_existing_content():
    target = candidate()
    backend = FakeBackend()
    index = CandidateIndex(backend)
    index.stage(target)
    backend.rows[target.records[0].row_id]["payload"]["source_text"] = "unexpected"
    with pytest.raises(VectorError, match="VECTOR_MANIFEST_MISMATCH"):
        index.stage(target)
    assert backend.writes == 1


def test_candidate_objects_cannot_diverge_from_manifest_before_write():
    target = candidate()
    forged = replace(target, records=target.records[:-1])
    backend = FakeBackend()
    with pytest.raises(VectorError, match="VECTOR_MANIFEST_MISMATCH"):
        CandidateIndex(backend).stage(forged)
    assert backend.writes == 0


def test_delete_one_attempt_leaves_other_attempt_user_and_version_intact():
    backend = FakeBackend()
    index = CandidateIndex(backend)
    targets = [
        candidate(),
        candidate(),
        candidate(identity=replace(IDENTITY, user_id=uuid4())),
        candidate(identity=replace(IDENTITY, version_id=uuid4())),
    ]
    for target in targets:
        index.stage(target)
    index.delete_candidate(targets[0].manifest.scope)
    for target in targets[1:]:
        assert index.verify(target.manifest)
    assert len(backend.rows) == 9


def test_search_rejects_backend_result_outside_target_scope():
    backend = FakeBackend()
    index = CandidateIndex(backend)
    own, other = candidate(), candidate(identity=replace(IDENTITY, user_id=uuid4()))
    verified = index.stage(own)
    index.stage(other)
    backend.search_rows = [
        {"entity": other.records[0].entity(other.manifest.scope), "distance": 1.0}
    ]
    with pytest.raises(VectorError, match="VECTOR_MANIFEST_MISMATCH"):
        index.search(verified, list(own.records[0].vector), PROFILE)


def test_healthy_empty_search_is_empty_but_failure_is_not():
    backend = FakeBackend()
    index = CandidateIndex(backend)
    target = candidate()
    verified = index.stage(target)
    backend.search_rows = []
    assert index.search(verified, list(target.records[0].vector), PROFILE) == []
    backend.search = Mock(side_effect=VectorError("VECTOR_UNAVAILABLE"))
    with pytest.raises(VectorError, match="VECTOR_UNAVAILABLE"):
        index.search(verified, list(target.records[0].vector), PROFILE)
    backend.search.assert_called_once()


def test_query_profile_must_match_and_results_include_original_location():
    backend = FakeBackend()
    index = CandidateIndex(backend)
    target = candidate()
    verified = index.stage(target)
    with pytest.raises(VectorError, match="VECTOR_BUILD_MISMATCH"):
        index.search(verified, list(target.records[0].vector), preset("bge-m3").embedding)
    hit = index.search(verified, list(target.records[0].vector), PROFILE)[0]
    assert hit["payload"]["source_text"] == "每日150元。"
    assert hit["payload"]["span"]["start_line"] == 3
    assert hit["payload"]["identity"]["user_id"] == str(IDENTITY.user_id)


@pytest.mark.parametrize(
    "vector", [[0.0] * 512, [1.0] * 511, [float("nan")] * 512, [float("inf")] * 512]
)
def test_invalid_vectors_are_rejected_before_storage(vector):
    with pytest.raises(VectorError, match="VECTOR_INVALID_VECTOR"):
        normalized_vector(vector)


def test_payload_byte_limit_is_not_silent_truncation():
    build = configuration(size=384, overlap=48)
    chunks = build_chunks(parse("正文"), IDENTITY, build, CharacterTokenizer())
    long_text = "x" * 70000
    digest = hashlib.sha256(long_text.encode()).hexdigest()
    large = replace(
        chunks.chunks[0],
        source_text=long_text,
        embedding_text=long_text,
        content_sha256=digest,
        embedding_sha256=digest,
    )
    with pytest.raises(VectorError, match="VECTOR_PAYLOAD_TOO_LARGE"):
        make_candidate(replace(chunks, chunks=(large,)), build, [[1.0] + [0.0] * 511], uuid4())


def test_scope_filter_uses_full_fixed_identity_and_json_quoted_values():
    scope = candidate().manifest.scope
    expr = scope_filter(scope)
    assert all(
        f"{key} == {json.dumps(value)}" in expr for key, value in scope.scalar_fields.items()
    )


def test_client_is_dedicated_and_disables_transport_retry_with_rpc_deadline(monkeypatch):
    factory = Mock()
    monkeypatch.setitem(sys.modules, "pymilvus", SimpleNamespace(MilvusClient=factory))
    backend = MilvusBackend.connect(VectorSettings(timeout_seconds=3))
    opts = factory.call_args.kwargs
    assert opts["dedicated"] is True
    assert opts["grpc_options"]["grpc.enable_retries"] == 0
    assert (
        json.loads(opts["grpc_options"]["grpc.service_config"])["methodConfig"][0]["timeout"]
        == "3s"
    )
    method = Mock(return_value="ok")
    assert backend._rpc(method, collection_name=COLLECTION) == "ok"
    method.assert_called_once_with(
        timeout=None, retry_times=0, retry_on_rate_limit=False, collection_name=COLLECTION
    )


def test_pinned_sdk_rpc_error_is_called_once_without_implicit_retry():
    import grpc
    from pymilvus.decorators import retry_on_rpc_failure

    class Failure(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "offline injected error"

    calls = []

    class Injected:
        @retry_on_rpc_failure()
        def rpc(self, **kwargs):
            calls.append(kwargs)
            raise Failure()

    with pytest.raises(VectorError, match="VECTOR_UNAVAILABLE"):
        MilvusBackend(VectorSettings(), Mock())._rpc(Injected().rpc)
    assert len(calls) == 1


def test_schema_mismatch_fails_without_drop_or_recreate():
    client = Mock()
    client.has_collection.return_value = True
    client.describe_collection.return_value = {
        "description": "another encoding space",
        "fields": [],
    }
    with pytest.raises(VectorError, match="VECTOR_COLLECTION_MISMATCH"):
        MilvusBackend(VectorSettings(), client).ensure_collection()
    client.drop_collection.assert_not_called()
    client.create_collection.assert_not_called()


def test_module_import_does_not_connect_or_import_sdk_models():
    code = (
        "import sys; import minimew.vector_store.adapter; import minimew.vector_store.milvus; "
        "assert not any(k in sys.modules for k in "
        "['pymilvus','grpc','torch','sentence_transformers'])"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)


@pytest.mark.parametrize("bad", ["no_entity", "no_score", "nan_score", "too_many"])
def test_malformed_search_results_are_typed_errors(bad):
    target = candidate()
    backend = FakeBackend()
    index = CandidateIndex(backend)
    verified = index.stage(target)
    hit = {"entity": target.records[0].entity(target.manifest.scope), "distance": 1.0}
    if bad == "no_entity":
        del hit["entity"]
    elif bad == "no_score":
        del hit["distance"]
    elif bad == "nan_score":
        hit["distance"] = float("nan")
    backend.search_rows = [hit, hit] if bad == "too_many" else [hit]
    with pytest.raises(VectorError, match="VECTOR_INVALID_RESULT"):
        index.search(verified, list(target.records[0].vector), PROFILE, top_k=1)
