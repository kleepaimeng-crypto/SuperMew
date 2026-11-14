import asyncio
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from minimew.hitl.checkpoint import BufferedSaver, decode, encode, validate_document
from minimew.hitl.policy import HitlPolicy


def test_buffered_checkpoint_roundtrip_resumes_interrupt_without_replaying_prior_node():
    async def scenario():
        calls = []

        def before(state):
            calls.append("before")
            return {"question": "国内还是海外？"}

        def pause(state):
            answer = interrupt(state["question"])
            return {"answer": answer}

        graph = StateGraph(dict)
        graph.add_node("before", before)
        graph.add_node("pause", pause)
        graph.add_edge(START, "before")
        graph.add_edge("before", "pause")
        graph.add_edge("pause", END)
        saver = BufferedSaver()
        config = {"configurable": {"thread_id": str(uuid4())}}
        result = await graph.compile(checkpointer=saver).ainvoke({}, config)
        assert result["__interrupt__"][0].value == "国内还是海外？"
        # 新 saver/新图实例，仅有 JSON 检查点；不共享旧图对象和内存。
        saved = saver.document()
        import json

        saved = json.loads(json.dumps(saved))
        fresh = BufferedSaver(saved)
        result = await graph.compile(checkpointer=fresh).ainvoke(Command(resume="国内"), config)
        assert result["answer"] == "国内" and calls == ["before"]
        foreign = {"configurable": {"thread_id": str(uuid4())}}
        with pytest.raises(ValueError, match="Run mismatch"):
            await fresh.aget_tuple(foreign)

    asyncio.run(scenario())


def test_checkpoint_after_parallel_aggregation_preserves_pieces_and_does_not_replan(monkeypatch):
    import json
    from dataclasses import replace

    from test_knowledge import invoke, setup_service

    from minimew.execution.service import RunBudget
    from minimew.execution.tools import ToolContext
    from minimew.hitl.policy import PauseRequested
    from minimew.rag.reasoning import Grade, Plan, ReasoningPolicy, RepairPolicy

    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)
    plans, original_grades = [], []

    async def model(purpose, data, schema):
        if schema is Plan:
            plans.append(data)
            return Plan(questions=("餐费条件", "住宿条件"))
        if data["question"] == "餐费":
            original_grades.append(data)
            if len(original_grades) == 1:
                return Grade(status="insufficient", supported=(), missing=("条件",), decompose=True)
            if len(original_grades) == 2:
                return Grade(status="ambiguous", supported=(), missing=("国内还是海外？",))
        return Grade(
            status="sufficient", supported=(data["materials"][0]["citation_id"],), missing=()
        )

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(),
        hitl=HitlPolicy(),
        model_caller=model,
        run_budget=RunBudget(reasoning=True),
    )

    async def scenario():
        with pytest.raises(PauseRequested) as caught:
            await invoke(registry, context)
        assert len(BufferedSaver(caught.value.graph).state()["pieces"]) == 2
        fresh = registry.bind_context(
            ToolContext(
                context.user_id,
                context.thread_id,
                context.run_id,
                context.capabilities,
                reasoning=context.reasoning,
                repairs=context.repairs,
                hitl=context.hitl,
                model_caller=model,
                run_budget=RunBudget(**vars(context.run_budget)),
                checkpoint={"graph": caught.value.graph, "answer": "国内"},
            )
        )
        fresh.tool_sessions["knowledge_search"].scope = context.tool_sessions[
            "knowledge_search"
        ].scope
        result = json.loads(await invoke(registry, fresh))
        assert result["assessment"]["status"] == "sufficient"
        assert result["clarification"] == "国内"
        assert len(plans) == 1 and len(original_grades) == 3
        assert search.await_count == fresh.run_budget.retrieval_queries == 4
        assert scoped.await_count == 1 and fresh.run_budget.planner_used

    asyncio.run(scenario())


def test_checkpoint_codec_rejects_clients_unknown_classes_and_oversize():
    for value in (object(), lambda: None):
        with pytest.raises(ValueError):
            encode(value)
    with pytest.raises(ValueError):
        decode({"@": "os.system", "v": "anything"})
    with pytest.raises(ValueError):
        validate_document({"version": 2})
    with pytest.raises(ValueError):
        validate_document({"version": 1, "data": "a" * 8_388_608})


@pytest.mark.parametrize("mode", ["pause", "no_prompt", "blank_prompt", "legacy", "provider_error"])
def test_knowledge_graph_pause_does_not_commit_evidence_or_repeat_initial_search(monkeypatch, mode):
    import json
    from dataclasses import replace

    from test_knowledge import invoke, setup_service

    from minimew.execution.provider import ProviderFailure
    from minimew.execution.service import RunBudget
    from minimew.execution.tools import ToolContext, ToolFailure
    from minimew.hitl.policy import PauseRequested
    from minimew.rag.reasoning import Grade, ReasoningPolicy, RepairPolicy

    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)
    calls = []

    async def model(purpose, data, schema):
        calls.append(data)
        if mode == "provider_error":
            raise ProviderFailure()
        return Grade(
            status="ambiguous" if len(calls) == 1 else "sufficient",
            supported=(data["materials"][0]["citation_id"],),
            missing=(" " if mode == "blank_prompt" else "国内还是海外？",)
            if len(calls) == 1 and mode != "no_prompt"
            else (),
        )

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(),
        hitl=None if mode == "legacy" else HitlPolicy(),
        model_caller=model,
        run_budget=RunBudget(reasoning=True),
    )

    async def scenario():
        if mode == "provider_error":
            with pytest.raises(ToolFailure):
                await invoke(registry, context)
            assert not context.evidence.items
            return
        if mode != "pause":
            result = json.loads(await invoke(registry, context))
            assert result["assessment"]["status"] == "ambiguous"
            return
        with pytest.raises(PauseRequested) as caught:
            await invoke(registry, context)
        assert context.evidence.items == ()
        assert context.run_budget.retrieval_queries == 1
        fresh = registry.bind_context(
            ToolContext(
                context.user_id,
                context.thread_id,
                context.run_id,
                context.capabilities,
                reasoning=context.reasoning,
                repairs=context.repairs,
                hitl=context.hitl,
                model_caller=model,
                run_budget=RunBudget(**vars(context.run_budget)),
                checkpoint={"graph": caught.value.graph, "answer": "国内"},
            )
        )
        fresh.tool_sessions["knowledge_search"].scope = context.tool_sessions[
            "knowledge_search"
        ].scope
        result = json.loads(await invoke(registry, fresh))
        assert result["assessment"]["status"] == "sufficient"
        assert fresh.evidence.items and result["matches"][0]["citation_id"] == "E1"
        assert fresh.run_budget.retrieval_queries == search.await_count == 2
        assert scoped.await_count == 1
        assert calls[-1]["aspects"][0]["clarification"] == "国内"

    asyncio.run(scenario())
