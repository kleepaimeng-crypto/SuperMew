"""6.7案例真源、覆盖统计与受控Provider；不加载模型或联网。"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rag_cases import (  # noqa: E402
    Case,
    ScriptedModel,
    coverage,
    fingerprint,
    load_cases,
    portable_observation,
    timings,
)


def test_case_provenance_is_complete_and_fingerprint_changes_with_expectations():
    suite, source, digest = load_cases()
    assert len(suite.cases) == 11 and len({c.id for c in suite.cases}) == 11
    assert all(source.count(a) == 1 for c in suite.cases for a in c.required)
    assert not any(a in source for c in suite.cases for a in c.unavailable)
    value = {"cases": suite.model_dump(mode="json"), "source": source}
    assert fingerprint(value) == digest
    value["source"] += "\n补充条款"
    assert fingerprint(value) != digest


def test_saved_observation_matches_the_current_case_and_source_fingerprint():
    suite, source, digest = load_cases()
    path = Path(__file__).resolve().parents[1] / "examples/rag/chapter06-observation.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["suite_fingerprint"] == digest
    assert saved["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert {o["case"] for o in saved["runs"]} == {c.id for c in suite.cases}
    assert len(saved["retrieval"]) == 28
    assert saved["reasoning_mode"] == "controlled_fixture" and not saved["model_quality_evaluated"]


def test_repair_observation_preserves_original_failure_evidence_and_case_expectations():
    root = Path(__file__).resolve().parents[1] / "examples/rag"
    old = json.loads((root / "chapter06-observation.json").read_text(encoding="utf-8"))
    new = json.loads((root / "chapter06-repair-observation.json").read_text(encoding="utf-8"))
    assert old["suite_fingerprint"] == new["suite_fingerprint"] == load_cases()[2]
    assert old["source_sha256"] == new["source_sha256"]
    before = next(r for r in old["runs"] if r["case"] == "cross")
    after = next(r for r in new["runs"] if r["case"] == "cross")
    assert before["coverage"]["coverage"] == 2 / 3
    assert after["coverage"]["coverage"] == 1 and after["coverage"]["missing"] == []
    assert before["model_attempts"] == after["model_attempts"] == 7
    assert new["run_policies"]["cross"]["schema_version"] == 4
    assert not new["model_quality_evaluated"]


@pytest.mark.parametrize("mode", ["duplicate", "empty", "query", "rewrite"])
def test_invalid_expectations_and_controlled_route_inputs_are_rejected(mode):
    suite, _, _ = load_cases()
    value = suite.cases[0].model_dump(mode="json")
    if mode == "duplicate":
        value["required"] *= 2
    elif mode == "empty":
        value["required"] = [" "]
    elif mode == "query":
        value.update(mode="plan", queries=["same", "same"])
    else:
        value.update(mode="rewrite", rewrite=None)
    with pytest.raises(ValidationError):
        Case.model_validate_json(json.dumps(value))


def material(source, start, end):
    return {
        "start": start,
        "end": end,
        "text": source[start:end],
        "content_sha256": hashlib.sha256(source[start:end].encode()).hexdigest(),
    }


@pytest.mark.parametrize(
    "spans,expected", [([(0, 3), (3, 6)], 1), ([(0, 2), (4, 6)], 0), ([(0, 4), (1, 6), (0, 4)], 1)]
)
def test_coverage_uses_actual_source_intervals_without_counting_gaps_or_duplicates(spans, expected):
    source = "AABBCC"
    case = SimpleNamespace(required=("BB",), unavailable=())
    result = coverage(case, source, [material(source, a, b) for a, b in spans])
    assert result["coverage"] == expected


@pytest.mark.parametrize("mode", ["text", "hash", "span"])
def test_forged_provenance_is_rejected(mode):
    match = material("AABBCC", 0, 6)
    match[{"text": "text", "hash": "content_sha256", "span": "start"}[mode]] = (
        1 if mode == "span" else "invalid"
    )
    with pytest.raises(ValueError):
        coverage(SimpleNamespace(required=("BB",), unavailable=()), "AABBCC", [match])


def test_unknown_answer_is_not_scored_as_complete_and_small_sample_has_no_p95():
    result = coverage(SimpleNamespace(required=(), unavailable=("CC",)), "AA", [])
    assert result["coverage"] is None and result["unsupported_claims"] == ["CC"]
    result = timings([0.1, 0.5, 0.3])
    assert result["sample_median_seconds"] == 0.3 and result["p95"] is None
    with pytest.raises(ValueError):
        timings([float("nan")])


def test_controlled_retry_is_counted_as_an_attempt_not_real_model_quality():
    from minimew.execution.provider import ProviderFailure

    suite, _, _ = load_cases()
    case = next(c for c in suite.cases if c.id == "retry")
    model = ScriptedModel(case)
    request = SimpleNamespace(
        purpose="grade",
        messages=[
            SimpleNamespace(
                content=json.dumps(
                    {"question": case.question, "materials": [{"citation_id": "E1"}]}
                )
            )
        ],
    )

    async def scenario():
        with pytest.raises(ProviderFailure) as caught:
            _ = [e async for e in model.stream(request)]
        assert caught.value.retryable
        events = [e async for e in model.stream(request)]
        assert json.loads(events[0].turn.content)["status"] == "sufficient"

    asyncio.run(scenario())
    assert len(model.attempts) == 2 and model.retry_failures == 1


@pytest.mark.parametrize("phase", ["initial", "aggregate"])
def test_final_grade_sees_only_budget_survivors_not_discarded_candidates(monkeypatch, phase):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from test_knowledge import invoke, setup_service

    import minimew.knowledge as knowledge
    from minimew.evidence import EvidenceLimit
    from minimew.execution.service import RunBudget
    from minimew.rag.protocol import Candidate, MaterialGroup, RetrievalResult, RetrievalTrace
    from minimew.rag.reasoning import Grade, Plan, ReasoningPolicy

    _, registry, context, _, _, _, rows = setup_service(monkeypatch)
    result = RetrievalResult(
        tuple(MaterialGroup((Candidate(r, dense=r["score"]),)) for r in rows),
        RetrievalTrace("dense"),
    )
    monkeypatch.setattr(
        knowledge,
        "RagPipeline",
        lambda *a, **k: SimpleNamespace(search=AsyncMock(return_value=result)),
    )
    propose = context.evidence.propose

    def limited(source, pending):
        if str(source.chunk_id) == rows[2]["chunk_id"]:
            raise EvidenceLimit()
        return propose(source, pending)

    context.evidence.propose = limited
    originals = []

    async def caller(purpose, data, schema):
        assert context.evidence.items == ()
        if schema is Plan:
            return Plan(questions=("方面一", "方面二"))
        assert len(data["materials"]) == 2
        assert rows[2]["chunk_id"] not in {m["chunk_id"] for m in data["materials"]}
        if data["question"] == "餐费":
            originals.append(data)
            if phase == "aggregate":
                return Grade(
                    status="insufficient",
                    supported=(),
                    missing=("第三方面材料未保留",),
                    decompose=len(originals) == 1,
                )
        return Grade(status="sufficient", supported=("E1",), missing=())

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        model_caller=caller,
        run_budget=RunBudget(reasoning=True, rewrite_used=phase == "aggregate"),
    )
    answer = json.loads(asyncio.run(invoke(registry, context)))
    assert answer["limited"] and len(context.evidence.items) == 2
    assert len(originals) == (2 if phase == "aggregate" else 1)
    assert answer["assessment"]["status"] == (
        "insufficient" if phase == "aggregate" else "sufficient"
    )


def test_portable_export_keeps_gaps_and_excludes_private_or_device_fields():
    observation = {
        "suite_fingerprint": "case-hash",
        "source_sha256": "source-hash",
        "embedding": {},
        "reranker": {},
        "scope_note": "controlled",
        "retrieval": [
            {
                "case": "c",
                "route": "dense",
                "policy": {},
                "coverage": {"missing": ["条件"]},
                "limited": True,
                "selection_stable": True,
                "trace": {"fallback": None},
                "materials": [
                    {"start": 1, "end": 3, "content_sha256": "h", "chunk_id": "private-id"}
                ],
                "timing": [999],
                "secret": "do-not-export",
            }
        ],
        "runs": [
            {
                "case": "c",
                "sufficient_with_missing_anchors": True,
                "frozen_policy": {},
                "secret": "do-not-export",
                "controlled_end_to_end_seconds": 999,
            }
        ],
        "password": "do-not-export",
        "scope": ["private-id"],
    }
    exported = portable_observation(observation)
    encoded = json.dumps(exported)
    assert "do-not-export" not in encoded and "private-id" not in encoded and "999" not in encoded
    assert exported["retrieval"][0]["coverage"]["missing"] == ["条件"]
    assert exported["runs"][0]["sufficient_with_missing_anchors"] is True
    assert not exported["model_quality_evaluated"]
