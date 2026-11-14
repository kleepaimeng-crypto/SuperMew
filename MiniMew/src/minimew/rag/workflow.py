"""Run内有界StateGraph；不配置checkpointer、不提交Evidence或Run终态。"""

import asyncio
import logging
import operator
import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from minimew.embeddings.errors import EmbeddingError
from minimew.execution.diagnostics import capture, emit
from minimew.execution.provider import ProviderFailure
from minimew.rag.protocol import MaterialGroup, RetrievalResult
from minimew.rag.reasoning import Grade, Plan, Rewrite, grade_materials
from minimew.vector_store.manifest import VectorError

logger = logging.getLogger(__name__)


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
    representatives: tuple
    supported: frozenset


async def run_workflow(context, question, initial, retrieve, prepare):
    budget = context.run_budget
    slots = asyncio.Semaphore(context.reasoning.concurrency)

    def keys(result, draft, supported=None):
        identities = {
            (str(m.version_id), str(m.chunk_id))
            for m in draft.matches
            if supported is None or m.citation_id in supported
        }
        return frozenset(
            c.key for g in result.groups for c in g.members if (c.key[0], c.key[2]) in identities
        )

    async def assess(state):
        draft, pending = prepare(state["result"])
        actual = keys(state["result"], draft)
        gaps = tuple(
            f"{label}的代表材料未被最终预算保留"
            for label, wanted in state.get("representatives", ())
            if not wanted <= actual
        )
        aspects = state.get("aspects", ())
        if gaps:
            aspects = (*aspects, {"question": question, "status": "insufficient", "missing": gaps})
        grade = (
            await grade_materials(context.model_caller, question, draft, aspects)
            if draft.matches
            else Grade(status="insufficient", supported=(), missing=("未找到相关材料",))
        )
        missing = (*state.get("failed", ()), *gaps)
        if len(missing) > 8:
            missing = (*missing[:7], f"另有{len(missing) - 7}项代表材料/子任务缺口")
        if missing and grade.status == "sufficient":
            grade = grade.model_copy(update={"status": "insufficient", "missing": missing})
        return {
            "draft": draft,
            "pending": pending,
            "grade": grade,
            "supported": keys(state["result"], draft, grade.supported),
        }

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
                return {
                    "pieces": (
                        (
                            state["index"],
                            result,
                            grade,
                            keys(result, draft, grade.supported) if grade else frozenset(),
                        ),
                    )
                }

            except (ProviderFailure, VectorError, EmbeddingError) as exc:
                emit(
                    logger,
                    "rag_subtask_failed",
                    context.run_id,
                    exc.code,
                    getattr(exc, "diagnostic", None) or capture("subtask", "subtask_failed", exc),
                    thread_id=context.thread_id,
                    subtask=state["index"] + 1,
                )
                return {"pieces": ((state["index"], None, exc.code, frozenset()),)}

    def combine(original, extras, preferred=()):
        if context.repairs is not None:
            from minimew.rag.selection import balanced

            return balanced(
                (original, *extras), preferred, context.rag_policy.final_k, original.trace
            )
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
        ), ()

    def aggregate(state):
        pieces = sorted(state["pieces"], key=lambda x: x[0])
        if all(p[1] is None for p in pieces):
            raise ProviderFailure("PROVIDER_ERROR")
        successful = [p for p in pieces if p[1] is not None]
        result, representatives = combine(
            state["result"],
            [p[1] for p in successful],
            (state["supported"], *(p[3] for p in successful)),
        )
        return {
            "result": result,
            "representatives": tuple(
                zip(
                    ("原问题", *(f"子问题{p[0] + 1}" for p in successful)),
                    representatives,
                    strict=True,
                )
            )
            if context.repairs
            else (),
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
                for i, result, grade, _ in pieces
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
        combined, representatives = combine(
            state["result"], [result], (state["supported"], frozenset())
        )
        return {
            "result": combined,
            "representatives": (
                *state.get("representatives", ()),
                *zip(("改写前材料", "改写检索"), representatives, strict=True),
            )
            if context.repairs
            else (),
            "phase": "rewritten",
        }

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
