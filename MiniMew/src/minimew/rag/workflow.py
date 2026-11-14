"""Run内有界StateGraph；不配置checkpointer、不提交Evidence或Run终态。"""

import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from minimew.execution.provider import ProviderFailure
from minimew.rag.protocol import MaterialGroup, RetrievalResult
from minimew.rag.reasoning import Grade, Rewrite, grade_materials


class State(TypedDict, total=False):
    result: object
    draft: object
    pending: tuple
    grade: object
    phase: str
    questions: tuple
    query: str
    index: int
    failed: tuple
    aspects: tuple


async def run_workflow(context, question, initial, retrieve, prepare):
    budget = context.run_budget

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
        return "rewrite" if not budget.rewrite_used else "finish"

    def combine(original, extras):
        groups = []
        seen = set()
        for result in (*extras, original):
            for group in result.groups:
                members = tuple(c for c in group.members if c.key not in seen)
                if not members:
                    continue
                groups.append(
                    MaterialGroup(
                        members, group.completed and len(members) == len(group.members), group.seeds
                    )
                )
                seen.update(c.key for c in members)
        # Preserve full groups; final Evidence budget is applied by the shared draft step.
        from minimew.rag.merging import select

        return RetrievalResult(
            select(groups, context.rag_policy.final_k, original.trace), original.trace
        )

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
    graph.add_node("rewrite", rewrite)
    graph.add_edge(START, "assess")
    graph.add_conditional_edges("assess", route, {"finish": END, "rewrite": "rewrite"})
    graph.add_edge("rewrite", "assess")
    from langsmith import tracing_context

    with tracing_context(enabled=False):
        result = await graph.compile().ainvoke(
            {"result": initial, "phase": "initial"},
            config={"recursion_limit": 16, "callbacks": []},
        )
    return result["draft"], result["pending"], result["grade"]
