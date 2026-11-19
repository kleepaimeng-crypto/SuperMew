import asyncio
import json
from dataclasses import replace

import pytest
from test_knowledge import invoke, setup_service

from minimew.execution.provider import ProviderFailure
from minimew.execution.service import RunBudget
from minimew.rag.reasoning import Grade, Plan, ReasoningPolicy, Rewrite


@pytest.mark.parametrize("mode", ["sufficient", "rewrite", "plan", "partial", "failure"])
def test_graph_uses_actual_draft_keeps_original_and_bounded_private_state(monkeypatch, mode):
    _, registry, context, _, _, search, _ = setup_service(monkeypatch)
    calls = []
    active = 0
    maximum = 0

    async def model(purpose, data, schema):
        nonlocal active, maximum
        assert context.evidence.items == ()
        calls.append((purpose, data))
        if schema is Plan:
            return Plan(questions=("餐费一", "餐费二", "餐费三", "餐费四"))
        if schema is Rewrite:
            return Rewrite(query="餐费适用条件")
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            if len(calls) == 1 and mode != "sufficient":
                return Grade(
                    status="insufficient",
                    supported=(),
                    missing=("住宿",),
                    decompose=mode in {"plan", "partial", "failure"},
                )
            if data["question"] != "餐费" and mode == "failure":
                raise ProviderFailure()
            if data["question"] == "餐费二" and mode == "partial":
                raise ProviderFailure()
            return Grade(
                status="sufficient", supported=(data["materials"][0]["citation_id"],), missing=()
            )
        finally:
            active -= 1

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        run_budget=RunBudget(reasoning=True),
        model_caller=model,
    )
    if mode == "failure":
        with pytest.raises(ProviderFailure, match="PROVIDER_ERROR"):
            asyncio.run(invoke(registry, context))
        assert context.evidence.items == ()
    else:
        result = json.loads(asyncio.run(invoke(registry, context)))
        assert result["assessment"]["status"] == (
            "insufficient" if mode == "partial" else "sufficient"
        )
        assert context.evidence.items and maximum <= 2
        assert context.run_budget.retrieval_queries <= 8
        assert len([c for c in calls if c[0] == "rewrite"]) <= 1
        assert len([c for c in calls if c[0] == "plan"]) <= 1
        assert calls[-1][1]["question"] == "餐费"


def test_rewrite_cannot_drop_identifier(monkeypatch):
    _, registry, context, _, _, _, _ = setup_service(monkeypatch)

    async def model(purpose, data, schema):
        if schema is Rewrite:
            return Rewrite(query="报销")
        return Grade(status="insufficient", supported=(), missing=("缺口",))

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        run_budget=RunBudget(reasoning=True),
        model_caller=model,
    )
    with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_RESPONSE"):
        asyncio.run(invoke(registry, context, query="E1024"))
    assert context.evidence.items == ()


def test_multiple_tool_calls_share_plan_rewrite_and_retrieval_budget(monkeypatch):
    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)
    calls = []

    async def model(purpose, data, schema):
        calls.append(purpose)
        if schema is Plan:
            return Plan(questions=("餐费", "住宿"))
        if schema is Rewrite:
            return Rewrite(query="餐费住宿规则")
        return Grade(status="insufficient", supported=(), missing=("条件",), decompose=True)

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        run_budget=RunBudget(reasoning=True),
        model_caller=model,
    )

    async def scenario():
        first = json.loads(await invoke(registry, context))
        second = json.loads(await invoke(registry, context))
        assert first["assessment"]["status"] == second["assessment"]["status"] == "insufficient"

    asyncio.run(scenario())
    assert calls.count("plan") == calls.count("rewrite") == 1
    assert context.run_budget.retrieval_queries == search.await_count == 5
    assert scoped.await_count == 1


def test_out_of_order_children_are_merged_in_plan_order_and_deduplicated(monkeypatch):
    _, registry, context, runtime, _, search, hits = setup_service(monkeypatch)
    calls = []
    final = []
    queries = {"first": 1, "second": 2, "third": 3}
    runtime.encoder.encode_query = lambda query: [float(queries.get(query, 0))] + [0.0] * 511

    async def retrieve(*args, **kwargs):
        marker = args[4][0]  # Retriever receives encoded query, not raw text.
        # First child completes last; duplicate material must not gain a second citation.
        if marker == 1:
            await asyncio.sleep(0.03)
        return [hits[{1: 2, 2: 1, 3: 2}.get(marker, 0)]]

    search.side_effect = retrieve

    async def model(purpose, data, schema):
        calls.append(purpose)
        if schema is Plan:
            return Plan(questions=("first", "second", "third"))
        if len(calls) == 1:
            return Grade(status="insufficient", supported=(), missing=("方面",), decompose=True)
        if data["question"] == "餐费":
            final.extend(m["chunk_id"] for m in data["materials"])
            assert [a["question"] for a in data["aspects"]] == ["first", "second", "third"]
        return Grade(
            status="sufficient", supported=(data["materials"][0]["citation_id"],), missing=()
        )

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        run_budget=RunBudget(reasoning=True),
        model_caller=model,
    )
    result = json.loads(asyncio.run(invoke(registry, context)))
    expected = [hits[i]["chunk_id"] for i in (2, 1, 0)]
    assert final == expected
    assert [m["chunk_id"] for m in result["matches"]] == expected
    assert [m["citation_id"] for m in result["matches"]] == ["E1", "E2", "E3"]


def test_deduplication_also_filters_group_seeds_before_budget_fallback(monkeypatch):
    from minimew.rag.pipeline import RagPipeline
    from minimew.rag.protocol import (
        Candidate,
        MaterialGroup,
        RagPolicy,
        RetrievalResult,
        RetrievalTrace,
    )

    _, registry, context, _, _, _, hits = setup_service(monkeypatch)
    members = tuple(Candidate(h, dense=h["score"]) for h in hits)

    async def pipeline(self, query, *args, **kwargs):
        group = (
            MaterialGroup(members, True, members[:2])
            if query == "餐费"
            else MaterialGroup(members[:1], seeds=members[:1])
        )
        return RetrievalResult((group,), RetrievalTrace("dense"))

    monkeypatch.setattr(RagPipeline, "search", pipeline)
    grades = 0

    async def model(purpose, data, schema):
        nonlocal grades
        if schema is Rewrite:
            return Rewrite(query="新检索")
        grades += 1
        return Grade(
            status="insufficient" if grades == 1 else "sufficient",
            supported=() if grades == 1 else (data["materials"][0]["citation_id"],),
            missing=("条件",) if grades == 1 else (),
        )

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        rag_policy=RagPolicy(final_k=2),
        run_budget=RunBudget(reasoning=True),
        model_caller=model,
    )
    result = json.loads(asyncio.run(invoke(registry, context)))
    assert [m["chunk_id"] for m in result["matches"]] == [h["chunk_id"] for h in hits[:2]]
    assert [m["citation_id"] for m in result["matches"]] == ["E1", "E2"]
