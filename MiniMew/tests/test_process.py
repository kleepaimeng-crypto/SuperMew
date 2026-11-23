"""公开报告的并行父子隔离与安全输出，不调用模型。"""

import asyncio
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from minimew.execution.process import ProcessReporter
from minimew.generated.run_event_v1 import ProcessDetailData, ProcessStepData


def test_parallel_steps_and_recreated_reporter_use_stable_owned_identity():
    async def scenario():
        events = []

        async def sink(data):
            ProcessStepData.model_validate(data)
            events.append(data)

        rid = uuid4()

        async def branch(i):
            reporter = ProcessReporter(rid, sink).tool("call")
            async with reporter.step("subquestion", str(i), query=str(i)) as parent:
                async with parent.reporter.step("dense", "dense") as step:
                    await asyncio.sleep(0)
                    step.finish(count=i)

        await asyncio.gather(branch(1), branch(2))
        starts = [x for x in events if x["status"] == "running"]
        assert len({x["step_id"] for x in starts}) == 4
        for event in events:
            assert event["tool_call_id"] == "call"
            if event["kind"] == "dense":
                parent = next(p for p in starts if p["step_id"] == event["parent_step_id"])
                assert parent["query"] in {"1", "2"}
        await branch(1)
        assert events[-4]["step_id"] == starts[0]["step_id"]

    asyncio.run(scenario())


def test_failure_does_not_serialize_exception_or_reasoning():
    async def scenario():
        events = []

        async def sink(data):
            events.append(data)

        with pytest.raises(ValueError):
            async with ProcessReporter(uuid4(), sink).step("model", "one"):
                raise ValueError("private secret")
        assert events[-1]["status"] == "failed"
        assert events[-1]["outcome"] == "PROCESS_STAGE_FAILED"
        assert "private secret" not in str(events)
        with pytest.raises(ValidationError):
            ProcessStepData.model_validate({**events[0], "sources": [{}] * 7})

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["sufficient", "rewrite", "plan", "partial", "failure"])
def test_formal_workflow_observation_preserves_original_routes(monkeypatch, mode):
    from dataclasses import replace

    import test_reasoning_graph as cases

    original = cases.setup_service
    events = []
    details = []

    async def sink(data):
        ProcessStepData.model_validate(data)
        events.append(data)

    async def detail_sink(data):
        ProcessDetailData.model_validate(data)
        details.append(data)

    def setup(patch):
        values = list(original(patch))
        values[2] = replace(
            values[2], process=ProcessReporter(values[2].run_id, sink, detail_sink).tool("call_1")
        )
        return values

    monkeypatch.setattr(cases, "setup_service", setup)
    cases.test_graph_uses_actual_draft_keeps_original_and_bounded_private_state(monkeypatch, mode)
    assert any(e["kind"] == "assessment" for e in events)
    results = [d for d in details if d["stage"] == "result"]
    if mode != "sufficient":
        assert any(d["phase"] == "initial" and d["missing"] == ["住宿"] for d in results)
    if mode == "rewrite":
        rewrites = [e for e in events if e["kind"] == "rewrite" and e["status"] == "completed"]
        assert rewrites and rewrites[-1]["summary"].startswith("改写查询：")
    if mode in {"plan", "partial", "failure"}:
        children = {e["step_id"] for e in events if e["kind"] == "subquestion"}
        assert len(children) == 4
        assert any(e["parent_step_id"] in children for e in events)
        assert any(d["questions"] == ["餐费一", "餐费二", "餐费三", "餐费四"] for d in results)
        assert all(d["phase"] == "child" for d in details if d["step_id"] in children)


def test_detail_is_bounded_public_facts_emitted_before_step_completion():
    async def scenario():
        journal = []

        async def sink(data):
            ProcessStepData.model_validate(data)
            journal.append(("step", data))

        async def detail_sink(data):
            ProcessDetailData.model_validate(data)
            journal.append(("detail", data))

        async with ProcessReporter(uuid4(), sink, detail_sink).step(
            "model", "model/2", purpose="model_decision", attempt=2
        ) as step:
            step.finish(purpose="tool_selection")
        assert [k for k, _ in journal] == ["step", "detail", "detail", "step"]
        assert journal[1][1]["purpose"] == "model_decision"
        assert journal[2][1]["purpose"] == "tool_selection"
        assert journal[2][1]["attempt"] == 2
        for patch in ({"missing": ["x" * 257]}, {"questions": [""]}, {"reasoning": "private"}):
            with pytest.raises(ValidationError):
                ProcessDetailData.model_validate({**journal[2][1], **patch})

    asyncio.run(scenario())


def test_document_details_distinguish_filename_section_version_and_old_scope(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from pydantic import TypeAdapter
    from test_knowledge import setup_service

    from minimew.documents.retrieval import RetrievalScope, ScopeDocument
    from minimew.rag.pipeline import process_source_details

    _, _, _, _, scoped, _, rows = setup_service(monkeypatch)
    scope = scoped.return_value
    identity = rows[0]["payload"]["identity"]
    scope = replace(
        scope,
        documents=(
            ScopeDocument(
                UUID(identity["document_id"]), UUID(identity["version_id"]), "部署指南.md", 3
            ),
        ),
        directory=(
            SimpleNamespace(
                sections=tuple(
                    SimpleNamespace(
                        section_id=r["payload"]["section_id"], title_path=("部署指南", str(i))
                    )
                    for i, r in enumerate(rows)
                )
            ),
        ),
    )
    sources = process_source_details([*rows, *rows], scope)
    assert len(sources) == 1
    assert sources[0]["name"] == "部署指南.md" and sources[0]["version_number"] == 3
    assert sources[0]["sections"] and len(sources[0]["sections"]) == len(
        set(sources[0]["sections"])
    )
    legacy = process_source_details(rows, replace(scope, documents=()))
    assert legacy[0]["name"] == "文档名称暂不可用" and legacy[0]["version_number"] == 0
    encoded = TypeAdapter(RetrievalScope).dump_json(replace(scope, directory=()))
    assert TypeAdapter(RetrievalScope).validate_json(encoded).documents == scope.documents
    old = TypeAdapter(RetrievalScope).dump_python(scoped.return_value, mode="json")
    old.pop("documents")
    assert TypeAdapter(RetrievalScope).validate_python(old).documents == ()


def test_executor_reports_actual_attempt_and_response_purpose_without_extra_model_calls(
    monkeypatch, model_configuration
):
    from unittest.mock import AsyncMock

    from execution_helpers import FakeProvider
    from test_deepseek import request
    from test_execution_retry import Repository, execute

    from minimew.execution.messages import ModelTurn
    from minimew.execution.provider import ProviderCompleted, ProviderFailure

    monkeypatch.setattr("minimew.execution.service.ProcessReporter", ProcessReporter)

    async def scenario():
        repo = Repository(request(model_configuration))
        repo.record_process = AsyncMock(return_value=True)
        calls = 0

        async def handler(data):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ProviderFailure(retryable=True)
            yield ProviderCompleted(ModelTurn("text", "answer"))

        assert await execute(repo, FakeProvider(handler)) == "succeeded"
        details = [
            call.args[1]
            for call in repo.record_process.await_args_list
            if call.kwargs.get("detail")
        ]
        assert calls == 2
        assert [(d["stage"], d["attempt"], d["purpose"]) for d in details] == [
            ("start", 1, "model_decision"),
            ("start", 2, "model_decision"),
            ("result", 2, "answer"),
        ]

    asyncio.run(scenario())


def test_model_turn_summary_exposes_bounded_public_tool_decisions_only():
    import json

    from minimew.execution.messages import ModelTurn, ToolCall
    from minimew.execution.service import _model_turn_summary

    search = ModelTurn(
        "tool_calls",
        "",
        tool_calls=(
            ToolCall(
                "c1",
                "knowledge_search",
                json.dumps({"query": "链路仿真启动前准备", "secret_hint": "do-not-leak"}),
            ),
        ),
    )
    summary = _model_turn_summary(search, "model_decision")
    assert summary == "决定调用：知识库检索（“链路仿真启动前准备”）"
    assert "do-not-leak" not in summary

    long_query = "甲" * 160
    bounded = _model_turn_summary(
        ModelTurn(
            "tool_calls",
            "",
            tool_calls=(ToolCall("c2", "knowledge_search", json.dumps({"query": long_query})),),
        ),
        "model_decision",
    )
    assert bounded.endswith("…”）")
    assert len(bounded) < 200

    multi = ModelTurn(
        "tool_calls",
        "",
        tool_calls=(
            ToolCall("c3", "knowledge_search", json.dumps({"query": "q"})),
            ToolCall("c4", "read_skill_resource", json.dumps({"resource_id": "guide.md"})),
        ),
    )
    assert _model_turn_summary(multi, "model_decision") == (
        "决定调用 2 项工具：知识库检索、读取任务参考资料"
    )
    assert _model_turn_summary(ModelTurn("text", "ok"), "model_decision") == (
        "已整理上下文，开始输出最终回答"
    )
    assert _model_turn_summary(ModelTurn("text", "ok"), "answer_repair") == (
        "已完成回答格式校验与修正"
    )
