import asyncio
import hashlib
import json
import time
from dataclasses import asdict, replace
from uuid import uuid4

import httpx2
import pytest
from execution_helpers import FakeProvider
from pydantic import SecretStr
from test_agent import completed, executor
from test_answer_repairs import data_with_policy
from test_deepseek import chunk, request, wire
from test_deepseek import transport as transport
from test_execution_retry import Repository
from test_knowledge import invoke, setup_service
from test_tools import Result, call, spec
from test_tools import context as tool_context

from minimew.evidence import EvidenceSource
from minimew.execution.deepseek import DeepSeekProvider
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderDelta
from minimew.execution.reasoning import RunModelCaller
from minimew.execution.service import AttemptProgress, RunBudget, RunExecutor
from minimew.execution.tools import ToolFailure, ToolRegistry
from minimew.model_control import ModelSnapshot
from minimew.rag.reasoning import FinalizationReserved, ReasoningPolicy, RepairPolicy


@pytest.mark.parametrize("mode", ["success", "changed_body", "ignored_finish"])
def test_two_tool_rounds_leave_final_answer_and_repair_without_exceeding_limits(
    model_configuration, mode
):
    async def scenario():
        data = data_with_policy(model_configuration)
        data = replace(
            data,
            snapshot=data.snapshot.model_copy(
                update={"repairs": RepairPolicy(finalization="reserved-v1")}
            ),
        )
        repo = Repository(data)
        seen, budgets = [], []
        text = "餐费上限150元。"
        source = EvidenceSource(
            **{
                key: uuid4()
                for key in (
                    "knowledge_base_id",
                    "document_id",
                    "version_id",
                    "chunk_id",
                    "section_id",
                )
            },
            title_path=("制度",),
            start=0,
            end=len(text),
            start_line=1,
            end_line=1,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            text=text,
        )

        async def handler(ctx, args):
            ctx.evidence.queried = True
            evidence = ctx.evidence.propose(source, ())
            if not ctx.evidence.items:
                ctx.evidence.commit((evidence,))
            for _ in range(4):
                ctx.run_budget.take_internal()
            budgets.append(ctx.run_budget)
            return Result(text="餐费上限150元。[E1]")

        async def answer(req):
            seen.append((req.purpose, req.force_answer))
            if len(seen) <= 2:
                assert not req.force_answer
                yield completed(call(f"call_{len(seen)}"))
            elif req.purpose == "repair":
                value = "## 文档依据\n餐费上限150元。[E1]"
                if mode == "changed_body":
                    value = value.replace("150", "200")
                yield ProviderDelta(value)
                yield ProviderCompleted(ModelTurn("text", value))
            elif mode == "ignored_finish":
                assert req.force_answer
                yield completed(call("forbidden"))
            else:
                assert req.force_answer
                yield ProviderCompleted(ModelTurn("text", "餐费上限150元。[E1]"))

        e = RunExecutor(
            repo,
            FakeProvider(answer),
            registry=ToolRegistry((spec(handler),)),
            capabilities=frozenset({"documents.read"}),
        )
        outcome = await e.execute(uuid4(), data.thread_id, data.run_id)
        assert outcome == ("succeeded" if mode == "success" else "failed")
        budget = budgets[-1]
        assert budget.model_requests == (3 if mode == "ignored_finish" else 4)
        assert budget.internal_requests == 8 and budget.tool_calls == 2
        assert budget.model_requests + budget.internal_requests <= 12
        assert len(budgets) == 2 and budgets[0] is budget
        assert repo.record_tool.await_count == 4
        repo.append_delta.assert_not_awaited()
        if mode == "success":
            assert repo.finish.await_args.kwargs["content"] == "## 文档依据\n餐费上限150元。[E1]"
        else:
            assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_INVALID_RESPONSE"

    asyncio.run(scenario())


def test_internal_reserve_is_distinct_from_hard_limit_and_survives_checkpoint():
    budget = RunBudget(reasoning=True, finalization=True, model_requests=2, internal_requests=8)
    with pytest.raises(FinalizationReserved):
        budget.take_internal()
    restored = RunBudget(**asdict(budget))
    assert restored.finalization and not restored.can_request_tools()
    restored.require_model()
    restored.model_requests += 1
    restored.require_model()
    restored.model_requests += 1
    with pytest.raises(ToolFailure) as error:
        restored.require_model()
    assert error.value.diagnostic.reason == "outer_model_limit"
    assert error.value.diagnostic.counts["total_model_requests"] == 12


@pytest.mark.parametrize("kind", ["outer", "total", "retrieval", "tools"])
def test_exhaustion_diagnostics_identify_limit_and_counts(kind):
    budget = RunBudget(reasoning=True)
    if kind == "outer":
        budget.model_requests = 4
        operation = budget.require_model
    elif kind == "total":
        budget.model_requests, budget.internal_requests = 2, 10
        operation = budget.require_model
    elif kind == "retrieval":
        budget.retrieval_queries = 8
        operation = budget.take_retrieval
    else:
        budget.tool_calls = 6

        def operation():
            budget.require_tools(1)

    with pytest.raises(ToolFailure) as error:
        operation()
    assert error.value.diagnostic.stage == "budget"
    assert error.value.diagnostic.reason.startswith(kind.rstrip("s"))
    assert set(error.value.diagnostic.counts) == set(budget.counts())


def test_graph_at_reserved_budget_returns_unverified_materials_without_provider_call(
    monkeypatch, model_configuration
):
    _, registry, context, _, _, search, _ = setup_service(monkeypatch)
    budget = RunBudget(reasoning=True, finalization=True, model_requests=2, internal_requests=8)
    data = data_with_policy(model_configuration)

    async def forbidden(req):
        raise AssertionError("Reserved budget must not start another model request")
        yield

    async def check():
        pass

    caller = RunModelCaller(
        FakeProvider(forbidden), data, budget, time.monotonic() + 10, check, ReasoningPolicy()
    )
    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(finalization="reserved-v1"),
        run_budget=budget,
        model_caller=caller,
    )
    result = json.loads(asyncio.run(invoke(registry, context)))
    assert result["matches"]
    assert result["assessment"]["status"] == "insufficient"
    assert result["assessment"]["supported"] == []
    assert "未完成验证" in result["assessment"]["missing"][0]
    assert search.await_count == 1 and budget.internal_requests == 8


def test_new_policy_is_frozen_and_legacy_repair_hash_unchanged(model_configuration):
    legacy = data_with_policy(model_configuration).snapshot
    raw = legacy.model_dump(mode="json", exclude_none=True)
    assert "finalization" not in raw["repairs"]
    expected = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert ModelSnapshot.model_validate_json(json.dumps(raw)).catalog_hash() == expected
    new = legacy.model_copy(update={"repairs": RepairPolicy(finalization="reserved-v1")})
    assert new.catalog_hash() != expected
    assert (
        ModelSnapshot.model_validate_json(new.model_dump_json()).repairs.finalization
        == "reserved-v1"
    )


def test_real_sdk_receives_no_tool_choice_for_forced_answer(transport, model_configuration):
    async def response(req):
        body = json.loads(req.content)
        assert body["tool_choice"] == "none" and body["tools"]
        return httpx2.Response(200, content=wire(chunk("最终回答"), chunk(finish="stop")))

    requests, clients = transport(response)

    async def scenario():
        repo = Repository(request(model_configuration))
        e = executor(repo, DeepSeekProvider(SecretStr("test-key")), lambda c, a: Result(text="x"))
        context = e.registry.bind_context(tool_context())
        req = replace(repo.data, tools=e.registry.visible(context), force_answer=True)
        events = [event async for event in e.provider.stream(req)]
        assert len(events) == 1 and events[0].turn.content == "最终回答"
        assert len(requests) == 1 and all(c.is_closed for c in clients)

    asyncio.run(scenario())


def test_repair_budget_denial_keeps_format_reason_and_does_not_log_draft(
    caplog, model_configuration
):
    async def scenario():
        repo = Repository(request(model_configuration))
        e = executor(repo, FakeProvider(None), lambda c, a: Result(text="x"))
        ctx = replace(tool_context(), repairs=RepairPolicy())
        budget = RunBudget(reasoning=True, model_requests=4, internal_requests=3)
        with pytest.raises(ToolFailure) as failure:
            await e._grounded_answer(
                repo.claimed,
                repo.data,
                ModelTurn("text", "PRIVATE-MODEL-DRAFT"),
                ctx,
                time.monotonic() + 5,
                AttemptProgress(),
                budget,
            )
        assert failure.value.diagnostic.reason == "outer_model_limit"
        assert failure.value.diagnostic.cause_code == "missing_answer_sections"
        assert failure.value.diagnostic.counts["outer_model_requests"] == 4
        assert "answer_validation_failed" in caplog.text
        assert "missing_answer_sections" in caplog.text
        assert "PRIVATE-MODEL-DRAFT" not in caplog.text
        assert budget.model_requests == 4

    asyncio.run(scenario())
