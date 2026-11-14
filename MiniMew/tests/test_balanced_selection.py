import asyncio
import json
from dataclasses import replace

import pytest
from test_knowledge import invoke, setup_service

from minimew.execution.service import RunBudget
from minimew.rag.protocol import (
    Candidate,
    MaterialGroup,
    RagPolicy,
    RetrievalResult,
    RetrievalTrace,
)
from minimew.rag.reasoning import Grade, Plan, ReasoningPolicy, RepairPolicy
from minimew.rag.selection import balanced


def pools_from(rows):
    candidates = tuple(Candidate(r, dense=r["score"]) for r in rows)
    original = RetrievalResult(
        tuple(MaterialGroup((c,)) for c in candidates), RetrievalTrace("dense")
    )
    # One child previously filled both slots before the other child was considered.
    first = RetrievalResult((MaterialGroup(candidates[:2], True, candidates[:2]),), original.trace)
    second = RetrievalResult((MaterialGroup(candidates[2:]),), original.trace)
    return original, first, second


def test_representatives_reserve_room_for_later_pools_and_use_source_keys(monkeypatch):
    _, _, _, _, _, _, rows = setup_service(monkeypatch)
    pools = pools_from(rows)
    selected, representatives = balanced(pools, (frozenset(),) * 3, 3, RetrievalTrace("dense"))
    assert {c.key for g in selected.groups for c in g.members} == {
        c.key for g in pools[0].groups for c in g.members
    }
    assert all(r <= {c.key for g in selected.groups for c in g.members} for r in representatives)
    assert len(selected.rows) == len({r["chunk_id"] for r in selected.rows}) == 3


@pytest.mark.parametrize("limit", [1, 2])
def test_budget_loss_of_representative_cannot_be_hidden_by_sufficient_grader(monkeypatch, limit):
    import minimew.knowledge as knowledge

    _, registry, context, _, _, _, rows = setup_service(monkeypatch)
    initial, first, second = pools_from(rows)

    async def pipeline(self, query, *args, **kwargs):
        return {"餐费": initial, "方面一": first, "方面二": second}[query]

    monkeypatch.setattr(knowledge.RagPipeline, "search", pipeline)
    calls = []

    async def model(purpose, data, schema):
        calls.append(data)
        if schema is Plan:
            return Plan(questions=("方面一", "方面二"))
        if len(calls) == 1:
            return Grade(status="insufficient", supported=(), missing=("方面",), decompose=True)
        return Grade(
            status="sufficient", supported=(data["materials"][0]["citation_id"],), missing=()
        )

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(),
        rag_policy=RagPolicy(final_k=limit),
        model_caller=model,
        run_budget=RunBudget(reasoning=True, rewrite_used=True),
    )
    result = json.loads(asyncio.run(invoke(registry, context)))
    assert result["assessment"]["status"] == "insufficient"
    assert result["limited"] and len(result["matches"]) <= limit
    assert any("代表材料" in m for m in result["assessment"]["missing"])
    assert any(a["status"] == "insufficient" for a in calls[-1]["aspects"])
