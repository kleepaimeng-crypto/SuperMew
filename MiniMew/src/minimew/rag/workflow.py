"""Run内有界StateGraph；不配置checkpointer、不提交Evidence或Run终态。"""

import asyncio
import operator
import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from minimew.embeddings.errors import EmbeddingError
from minimew.execution.provider import ProviderFailure
from minimew.rag.protocol import MaterialGroup, RetrievalResult
from minimew.rag.reasoning import Grade, Plan, Rewrite, grade_materials
from minimew.vector_store.manifest import VectorError


class State(TypedDict, total=False):
    result: object
    draft: object
    pending: tuple
    grade: object
    phase: str
    questions: tuple
    pieces: Annotated[tuple, operator.add]
    query: str
    index: int
    failed: tuple
    aspects: tuple


async def run_workflow(context, question, initial, retrieve, prepare):
    budget = context.run_budget
    slots = asyncio.Semaphore(context.reasoning.concurrency)

    async def assess(state):
        draft, pending = prepare(state["result"])
        grade = (
            await grade_materials(context.model_caller, question, draft, state.get("aspects", ()))
            if draft.matches
            else Grade(status="insufficient", supported=(), missing=("未找到相关材料",))
        )
        if state.get("failed") and grade.status == "sufficient":
            grade = grade.model_copy(update={"status": "insufficient", "missing": state["failed"]})
        return {"draft": draft, "pending": pending, "grade": grade}

    def route(state):
        grade = state["grade"]
        if grade.status != "insufficient":
            return "finish"
        if grade.decompose and state["phase"] == "initial" and not budget.planner_used:
            return "plan"
        return "rewrite" if not budget.rewrite_used else "finish"

    async def plan(state):
        budget.planner_used = True
        result = await context.model_caller(
            "plan", {"question": question, "missing": state["grade"].missing}, Plan
        )
        queries = tuple(q.strip() for q in result.questions)
        if any(not q or len(q) > 1024 or "\x00" in q for q in queries) or len(set(queries)) != len(
            queries
        ):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        return {"questions": queries}

    def dispatch(state):
        return [Send("child", {"query": q, "index": i}) for i, q in enumerate(state["questions"])]

    async def child(state):
        async with slots:
            try:
                result = await retrieve(state["query"])
                draft, _ = prepare(result)
                grade = (
                    await grade_materials(context.model_caller, state["query"], draft)
                    if draft.matches
                    else None
                )
                return {"pieces": ((state["index"], result, grade),)}

            except (ProviderFailure, VectorError, EmbeddingError) as exc:
                return {"pieces": ((state["index"], None, exc.code),)}

    def combine(original, extras):
        groups = []
        seen = set()
        for result in (*extras, original):
            for group in result.groups:
                members = tuple(c for c in group.members if c.key not in seen)
                if not members:
                    continue
                member_keys = {member.key for member in members}
                groups.append(
                    MaterialGroup(
                        members,
                        group.completed and len(members) == len(group.members),
                        tuple(seed for seed in group.seeds if seed.key in member_keys),
                    )
                )
                seen.update(c.key for c in members)
        # Preserve full groups; final Evidence budget is applied by the shared draft step.
        from minimew.rag.merging import select

        return RetrievalResult(
            select(groups, context.rag_policy.final_k, original.trace), original.trace
        )

    def aggregate(state):
        pieces = sorted(state["pieces"], key=lambda x: x[0])
        if all(p[1] is None for p in pieces):
            raise ProviderFailure("PROVIDER_ERROR")
        return {
            "result": combine(state["result"], [p[1] for p in pieces if p[1] is not None]),
            "phase": "aggregate",
            "failed": tuple(f"子问题{p[0] + 1}处理失败：{p[2]}" for p in pieces if p[1] is None),
            "aspects": tuple(
                {
                    "question": state["questions"][i],
                    "status": "failed"
                    if result is None
                    else grade.status
                    if grade
                    else "insufficient",
                    "missing": [grade]
                    if result is None
                    else grade.missing
                    if grade
                    else ["无相关材料"],
                }
                for i, result, grade in pieces
            ),
        }

    async def rewrite(state):
        budget.rewrite_used = True
        revised = await context.model_caller(
            "rewrite", {"question": question, "missing": state["grade"].missing}, Rewrite
        )
        query = revised.query.strip()
        anchors = re.findall(r"[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*", question)
        if not query or "\x00" in query or any(a.lower() not in query.lower() for a in anchors):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        result = await retrieve(query)
        return {"result": combine(state["result"], [result]), "phase": "rewritten"}

    graph = StateGraph(State)
    graph.add_node("assess", assess)
    graph.add_node("plan", plan)
    graph.add_node("child", child)
    graph.add_node("aggregate", aggregate)
    graph.add_node("rewrite", rewrite)
    graph.add_edge(START, "assess")
    graph.add_conditional_edges(
        "assess", route, {"finish": END, "plan": "plan", "rewrite": "rewrite"}
    )
    graph.add_conditional_edges("plan", dispatch, ["child"])
    graph.add_edge("child", "aggregate")
    graph.add_edge("aggregate", "assess")
    graph.add_edge("rewrite", "assess")
    from langsmith import tracing_context

    with tracing_context(enabled=False):
        result = await graph.compile().ainvoke(
            {"result": initial, "phase": "initial", "pieces": ()},
            config={"recursion_limit": 16, "callbacks": []},
        )
    return result["draft"], result["pending"], result["grade"]
