"""Run内有界StateGraph；可导出私有检查点缓冲，不写持久事实或Run终态。"""

import asyncio
import logging
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from minimew.embeddings.errors import EmbeddingError
from minimew.execution.diagnostics import capture, emit
from minimew.execution.process import report_step
from minimew.execution.provider import ProviderFailure
from minimew.rag.protocol import MaterialGroup, RetrievalResult
from minimew.rag.reasoning import (
    FinalizationReserved,
    Grade,
    Plan,
    Rewrite,
    grade_materials,
    validate_plan,
    validate_rewrite,
)
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
    clarification: str


async def run_workflow(context, question, initial, retrieve, prepare, *, initial_query=None):
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
        async with report_step(
            context.process, "assessment", "assessment/" + state["phase"], phase=state["phase"]
        ) as stage:
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
            if stage:
                stage.finish(
                    summary="证据评判结束",
                    count=len(draft.matches),
                    outcome=grade.status,
                    missing=grade.missing,
                )
        return {
            "draft": draft,
            "pending": pending,
            "grade": grade,
            "supported": keys(state["result"], draft, grade.supported),
        }

    def route(state):
        grade = state["grade"]
        if (
            grade.status == "ambiguous"
            and any(item.strip() for item in grade.missing)
            and context.hitl is not None
            and not budget.clarification_used
        ):
            return "clarify"
        if grade.status != "insufficient":
            return "finish"
        if budget.finalization and budget.internal_available() < 2:
            return "finish"
        if grade.decompose and state["phase"] == "initial" and not budget.planner_used:
            return "plan"
        return "rewrite" if not budget.rewrite_used else "finish"

    async def plan(state):
        budget.planner_used = True
        async with report_step(context.process, "plan", "plan", phase="initial") as stage:
            result = await context.model_caller(
                "plan", {"question": question, "missing": state["grade"].missing}, Plan
            )
            queries = validate_plan(result)
            if stage:
                stage.finish(summary="已拆解检索子问题", count=len(queries), questions=queries)
        return {"questions": queries}

    def dispatch(state):
        return [Send("child", {"query": q, "index": i}) for i, q in enumerate(state["questions"])]

    async def child(state):
        async with slots:
            async with report_step(
                context.process,
                "subquestion",
                f"child/{state['index']}",
                query=state["query"],
                phase="child",
            ) as stage:
                try:
                    result = await retrieve(
                        state["query"], process=stage.reporter if stage else None, step_key="search"
                    )
                    draft, _ = prepare(result)
                    async with report_step(
                        stage.reporter if stage else None, "assessment", "assessment", phase="child"
                    ) as grade_step:
                        grade = (
                            await grade_materials(context.model_caller, state["query"], draft)
                            if draft.matches
                            else None
                        )
                        if grade_step:
                            grade_step.finish(
                                summary="子问题证据评判结束",
                                count=len(draft.matches),
                                outcome=grade.status if grade else "insufficient",
                                missing=grade.missing if grade else ("未找到相关材料",),
                            )
                    if stage:
                        stage.finish(
                            summary="子问题处理结束",
                            count=len(draft.matches),
                            outcome=grade.status if grade else "insufficient",
                            missing=grade.missing if grade else ("未找到相关材料",),
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
                        getattr(exc, "diagnostic", None)
                        or capture("subtask", "subtask_failed", exc),
                        thread_id=context.thread_id,
                        identity=getattr(context, "execution_identity", None),
                        subtask=state["index"] + 1,
                    )
                    if stage:
                        stage.fail(exc.code)
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
        async with report_step(context.process, "rewrite", "rewrite", phase="rewritten") as stage:
            try:
                revised = await context.model_caller(
                    "rewrite", {"question": question, "missing": state["grade"].missing}, Rewrite
                )
                query = validate_rewrite(revised, question)
            except FinalizationReserved:
                if stage:
                    stage.finish(summary="模型预算已为最终回答预留，跳过补充改写")
                return {"phase": "rewrite_degraded"}
            except ProviderFailure as exc:
                if not state.get("supported"):
                    raise
                emit(
                    logger,
                    "rag_rewrite_degraded",
                    context.run_id,
                    exc.code,
                    getattr(exc, "diagnostic", None) or capture("rewrite", "rewrite_failed", exc),
                    thread_id=context.thread_id,
                    identity=getattr(context, "execution_identity", None),
                )
                if stage:
                    stage.fail(exc.code)
                note = f"补充改写检索未完成：{exc.code}"
                missing = (*state["grade"].missing, note)
                if len(missing) > 8:
                    missing = (*missing[:7], note)
                return {
                    "grade": state["grade"].model_copy(update={"missing": missing}),
                    "phase": "rewrite_degraded",
                }
            if stage:
                preview = " ".join(query.split())
                summary = f"改写查询：{preview[:240]}{'…' if len(preview) > 240 else ''}"
                stage.finish(summary=summary)
        result = await retrieve(query, process=context.process, step_key="rewrite-search")
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

    def clarify(state):
        # 此节点在 Command(resume) 时重跑；interrupt 之前没有外部调用或预算扣减。
        prompt = "请补充：" + "；".join(state["grade"].missing)
        answer = interrupt({"prompt": prompt[:1024]})
        return {"clarification": answer}

    async def clarified_search(state):
        budget.clarification_used = True
        # 保留原问题和补充的身份；只改变本次检索式，不改原用户 Message。
        query = f"{initial_query or question}\n用户补充：{state['clarification']}"
        if len(query) > 1024:
            # 超长原问题仍可澄清，但不能静默截断检索输入。
            from minimew.execution.tools import ToolFailure

            raise ToolFailure("KNOWLEDGE_QUERY_TOO_LONG")
        result = await retrieve(query, process=context.process, step_key="clarified-search")
        return {
            "result": result,
            "phase": "clarified",
            "failed": (),
            "aspects": ({"question": question, "clarification": state["clarification"]},),
            "representatives": (),
        }

    graph = StateGraph(State)
    graph.add_node("assess", assess)
    graph.add_node("plan", plan)
    graph.add_node("child", child)
    graph.add_node("aggregate", aggregate)
    graph.add_node("rewrite", rewrite)
    graph.add_node("clarify", clarify)
    graph.add_node("clarified_search", clarified_search)
    graph.add_edge(START, "assess")
    graph.add_conditional_edges(
        "assess", route, {"finish": END, "plan": "plan", "rewrite": "rewrite", "clarify": "clarify"}
    )
    graph.add_conditional_edges("plan", dispatch, ["child"])
    graph.add_edge("child", "aggregate")
    graph.add_edge("aggregate", "assess")
    graph.add_conditional_edges(
        "rewrite",
        lambda state: "finish" if state.get("phase") == "rewrite_degraded" else "assess",
        {"assess": "assess", "finish": END},
    )
    graph.add_edge("clarify", "clarified_search")
    graph.add_edge("clarified_search", "assess")
    from langsmith import tracing_context

    from minimew.hitl.checkpoint import BufferedSaver
    from minimew.hitl.policy import PauseRequested

    saved = context.checkpoint
    saver = BufferedSaver(saved["graph"] if saved else None) if context.hitl else None
    config = {"recursion_limit": 24 if context.hitl else 16, "callbacks": []}
    if saver:
        config["configurable"] = {"thread_id": context.execution_identity.checkpoint_key}

    with tracing_context(enabled=False):
        result = await graph.compile(checkpointer=saver).ainvoke(
            Command(resume=saved["answer"])
            if saved
            else {"result": initial, "phase": "initial", "pieces": ()},
            config=config,
        )
    if result.get("__interrupt__"):
        budget.clarification_used = True
        raise PauseRequested(result["__interrupt__"][0].value["prompt"], saver.document())
    return result["draft"], result["pending"], result["grade"]
