"""本地候选检查：只用自有样例范围，最终清理；不发布文档。"""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from minimew.documents.chunking import build_chunks
from minimew.documents.parsing import DocumentBuildError, parse_source
from minimew.documents.preview import build_preview
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.config import PROJECT_ROOT, EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.embeddings.tokenizer import LocalTokenizer
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import VectorError, make_candidate
from minimew.vector_store.milvus import MilvusBackend


def check(backend: MilvusBackend, settings: VectorSettings, path: Path) -> dict:
    runtime = EmbeddingRuntimeSettings()
    # 和分块预览复用同一条正式构建模块，不再建一条简化编码路径。
    from minimew.documents.profiles import preset

    profile = preset("bge-small-zh-v1.5").embedding
    tokenizer = LocalTokenizer.load(profile, runtime)
    build, chunks = build_preview(path, tokenizer)
    encoder = LocalEmbeddingAdapter.load_cpu(profile, runtime)
    vectors = encoder.encode_documents([row.embedding_text for row in chunks.chunks])
    candidate = make_candidate(chunks, build, vectors, uuid4())
    index = CandidateIndex(backend, settings.batch_size)
    scopes = []
    result = {"status": "error", "mode": "local_candidate_check", "published": False}
    try:
        backend.ensure_collection()
        scopes.append(candidate.manifest.scope)
        # 控制构造第一批已写、后续未写的情形，真实验证补齐/重复操作。
        seed = min(2, len(candidate.records))
        backend.upsert([r.entity(candidate.manifest.scope) for r in candidate.records[:seed]])
        verified = index.stage(candidate)
        assert index.stage(candidate) == verified
        other_attempt = make_candidate(chunks, build, vectors, uuid4())
        scopes.append(other_attempt.manifest.scope)
        index.stage(other_attempt)
        other_identity = replace(
            chunks.identity, user_id=uuid4(), document_id=uuid4(), version_id=uuid4()
        )
        parsed = parse_source(path.read_bytes(), build.source_format, chunks.source_sha256)
        other_chunks = build_chunks(parsed, other_identity, build, tokenizer)
        other_user = make_candidate(other_chunks, build, vectors, uuid4())
        scopes.append(other_user.manifest.scope)
        index.stage(other_user)
        observations = []
        for question, expected in [
            ("员工出差每天吃饭最多能报销多少钱？", "每人每日餐费报销上限"),
            ("其他城市住宿每晚的报销限额是多少？", "350 元"),
            ("太阳系最大的行星是什么？", None),
        ]:
            hits = index.search(verified, encoder.encode_query(question), profile)
            if expected is not None:
                assert hits and expected in hits[0]["payload"]["source_text"]
            observations.append({"question": question, "hits": hits})
        index.delete_candidate(candidate.manifest.scope)
        assert index.verify(other_attempt.manifest)
        assert index.verify(other_user.manifest)
        result.update(
            status="ok",
            manifest=asdict(candidate.manifest),
            expected_chunks=len(candidate.records),
            seeded_rows=seed,
            resumed_and_replayed=True,
            isolation_verified=True,
            observations=observations,
        )
    except (VectorError, EmbeddingError, DocumentBuildError) as exc:
        result["code"] = exc.code
    except AssertionError:
        result["code"] = "VECTOR_CHECK_EXPECTATION_FAILED"
    finally:
        failures = []
        for scope in scopes:
            try:
                index.delete_candidate(scope)
            except VectorError as exc:
                failures.append(exc.code)
        result["cleanup"] = "complete" if not failures else "failed"
        if failures:
            result.update(status="error", cleanup_errors=failures)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.parse_args()
    settings = VectorSettings()
    backend = None
    try:
        backend = MilvusBackend.connect(settings)
        result = check(backend, settings, PROJECT_ROOT / "examples/rag/expense-policy.md")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] == "ok" else 1
    except (VectorError, EmbeddingError, DocumentBuildError) as exc:
        print(json.dumps({"status": "error", "code": exc.code}))
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
