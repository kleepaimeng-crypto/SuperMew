import asyncio
import math
import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_document_chunking import IDENTITY, CharacterTokenizer, configuration, parse
from test_hybrid import HybridBackend

from minimew.documents.chunking import build_chunks
from minimew.documents.retrieval import RetrievalScope
from minimew.rag.merging import complete, select
from minimew.rag.pipeline import RagPipeline
from minimew.rag.protocol import Candidate, RagPolicy, RetrievalTrace
from minimew.rag.reranker import LocalReranker, RerankError
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.manifest import HYBRID_LAYOUT, make_candidate


def materials():
    build = configuration(size=64, overlap=0).model_copy(update={"index_layout": HYBRID_LAYOUT})
    chunks = build_chunks(
        parse(
            "# 制度\n## 餐费\n" + "员工出差每天报销餐费150元。" * 9 + "例外：公司供餐时不得报销。"
        ),
        IDENTITY,
        build,
        CharacterTokenizer(),
    )
    candidate = make_candidate(chunks, build, [[1.0] + [0.0] * 511 for _ in chunks.chunks], uuid4())
    backend = HybridBackend()
    index = CandidateIndex(backend)
    index.stage(candidate)
    scope = RetrievalScope(
        IDENTITY.user_id, (candidate.manifest,), datetime.now(UTC) + timedelta(minutes=1), (chunks,)
    )
    rows = [
        {"chunk_id": r["chunk_id"], "score": 0.1, "payload": r["payload"]}
        for r in backend.read(candidate.manifest.scope, 100)
    ]
    rows.sort(key=lambda r: r["payload"]["ordinal"])

    class Runtime:
        async def sync(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

    runtime = Runtime()
    runtime.index = index
    return scope, runtime, rows


def test_merge_recovers_exception_and_select_keeps_full_group_or_reports_limited():
    scope, runtime, rows = materials()
    assert 3 <= len(rows) <= 6
    seeds = tuple(Candidate(r, rrf=0.01) for r in rows[: len(rows) // 2 + 1])
    trace = RetrievalTrace("hybrid")
    groups = asyncio.run(complete(seeds, scope, runtime, RagPolicy(), trace))
    assert len(groups) == 1 and groups[0].completed and len(groups[0].members) == len(rows)
    assert "例外" in groups[0].members[-1].row["payload"]["source_text"]
    assert len(select(groups, 6, trace)[0].members) == len(rows)
    tiny = RetrievalTrace("hybrid")
    selected = select(groups, 1, tiny)
    assert tiny.limited and len(selected) == 1 and not selected[0].completed


def test_merge_requires_majority_and_never_crosses_version_or_fetches_over_budget():
    scope, runtime, rows = materials()
    one = (Candidate(rows[0], rrf=0.1),)
    assert not asyncio.run(complete(one, scope, runtime, RagPolicy(), RetrievalTrace("hybrid")))[
        0
    ].completed
    scope = replace(scope, directory=())
    seeds = tuple(Candidate(r, rrf=0.1) for r in rows[:2])
    assert all(
        not g.completed
        for g in asyncio.run(complete(seeds, scope, runtime, RagPolicy(), RetrievalTrace("hybrid")))
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("long", "RERANK_INPUT_TOO_LONG"),
        ("nan", "RERANK_INVALID_OUTPUT"),
        ("deadline", "RERANK_DEADLINE_EXCEEDED"),
    ],
)
def test_reranker_does_not_truncate_retry_or_accept_nonfinite(monkeypatch, mode, code):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext))
    calls = []

    def tokenize(*args, **kwargs):
        assert kwargs["truncation"] is False
        return {"input_ids": SimpleNamespace(shape=(1, 513 if mode == "long" else 5))}

    def model(**kwargs):
        calls.append(1)
        return SimpleNamespace(logits=SimpleNamespace(view=lambda n: [math.nan]))

    reranker = LocalReranker(tokenize, model)
    with pytest.raises(RerankError, match=code):
        reranker.score(
            "query", ["passage"], deadline=time.monotonic() - 1 if mode == "deadline" else None
        )
    assert len(calls) == (1 if mode == "nan" else 0)


@pytest.mark.parametrize("fail", [False, True])
def test_pipeline_scores_original_passages_and_records_fallback(fail):
    scope, runtime, rows = materials()
    runtime.encoder = SimpleNamespace(encode_query=lambda q: [1.0] + [0.0] * 511)

    class Scorer:
        def score(self, q, passages, **kwargs):
            assert all(p in [r["payload"]["embedding_text"] for r in rows] for p in passages)
            if fail:
                raise RerankError("RERANK_INPUT_TOO_LONG")
            return list(range(len(passages)))

    runtime.reranker = Scorer()
    retriever = AsyncMock(return_value=rows)
    result = asyncio.run(
        RagPipeline(object(), runtime, retriever=retriever).search(
            "query", scope, scope.user_id, RagPolicy(strategy="hybrid")
        )
    )
    assert result.trace.attempts == 1
    assert result.trace.fallback == ("RERANK_INPUT_TOO_LONG" if fail else None)
    assert all(r["score_kind"] == ("rrf" if fail else "rerank_raw") for r in result.rows)


@pytest.mark.parametrize("corrupt", [False, True])
def test_evidence_group_is_atomic_and_corruption_cannot_be_treated_as_budget(monkeypatch, corrupt):
    from test_knowledge import invoke, setup_service

    import minimew.knowledge as module
    from minimew.evidence import EvidenceLimit
    from minimew.execution.tools import ToolFailure
    from minimew.rag.protocol import MaterialGroup, RetrievalResult

    service, registry, context, runtime, scoped, search, rows = setup_service(monkeypatch)
    originals = tuple(Candidate(r, dense=r["score"]) for r in rows)
    if corrupt:
        import copy

        changed = copy.deepcopy(rows[2])
        changed["payload"]["source_text"] = "forged"
        members = (*originals[:2], Candidate(changed, dense=0.0))
    else:
        members = originals
    group = MaterialGroup(members, True, originals[:2])
    result = RetrievalResult((group,), RetrievalTrace("hybrid"))
    monkeypatch.setattr(
        module,
        "RagPipeline",
        lambda *a, **k: SimpleNamespace(search=AsyncMock(return_value=result)),
    )
    original_propose = context.evidence.propose

    def propose(source, pending):
        if not corrupt and str(source.chunk_id) == rows[2]["chunk_id"]:
            raise EvidenceLimit()
        return original_propose(source, pending)

    context.evidence.propose = propose
    if corrupt:
        with pytest.raises(ToolFailure, match="KNOWLEDGE_RESULT_INVALID"):
            asyncio.run(invoke(registry, context))
        assert context.evidence.items == ()
    else:
        import json

        answer = json.loads(asyncio.run(invoke(registry, context)))
        assert answer["limited"] and [x["citation_id"] for x in answer["matches"]] == ["E1", "E2"]
        assert len(context.evidence.items) == 2
