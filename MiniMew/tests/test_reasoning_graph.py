import asyncio
import json
from dataclasses import replace

import pytest
from test_knowledge import invoke, setup_service

from minimew.execution.provider import ProviderFailure
from minimew.execution.service import RunBudget
from minimew.execution.tools import ToolFailure
from minimew.rag.reasoning import Grade, Plan, ReasoningPolicy, Rewrite


@pytest.mark.parametrize("mode", ["sufficient", "rewrite"])
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
        with pytest.raises(ToolFailure):
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
    with pytest.raises(ToolFailure):
        asyncio.run(invoke(registry, context, query="E1024"))
    assert context.evidence.items == ()
