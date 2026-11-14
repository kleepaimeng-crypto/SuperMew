import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from execution_helpers import FakeProvider
from test_agent import completed
from test_deepseek import request
from test_execution_retry import Repository
from test_tools import Result, call, spec

from minimew.evidence import EvidenceSource
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderDelta, ProviderFailure
from minimew.execution.service import RunExecutor
from minimew.execution.tools import ToolRegistry
from minimew.model_control import ModelSnapshot
from minimew.rag.protocol import RagPolicy
from minimew.rag.reasoning import ReasoningPolicy, RepairPolicy


def data_with_policy(configuration, version=4):
    data = request(configuration)
    return replace(
        data,
        snapshot=ModelSnapshot(
            schema_version=version,
            answer=data.snapshot.answer,
            rag=RagPolicy(),
            reasoning=ReasoningPolicy(),
            repairs=RepairPolicy() if version == 4 else None,
        ),
    )


async def execute_case(configuration, mode="valid", version=4):
    from uuid import uuid4

    data = data_with_policy(configuration, version)
    repo = Repository(data)
    stages = []

    async def handler(context, args):
        text = "餐费上限150元，须持票据。"
        source = EvidenceSource(
            knowledge_base_id=uuid4(),
            document_id=uuid4(),
            version_id=uuid4(),
            chunk_id=uuid4(),
            section_id=uuid4(),
            title_path=("制度",),
            start=0,
            end=len(text),
            start_line=1,
            end_line=1,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            text=text,
        )
        context.evidence.queried = True
        context.evidence.commit((context.evidence.propose(source, ()),))
        if mode == "budget":
            for _ in range(10):
                context.run_budget.take_internal()
        return Result(text="餐费上限150元，须持票据。[E1]")

    async def answer(req):
        stages.append(req.purpose)
        if len(stages) == 1:
            yield completed(call())
        elif req.purpose == "repair":
            assert req.tools == () and req.private_output
            assert req.run_id == data.run_id and req.snapshot is data.snapshot
            if mode == "retry" and stages.count("repair") == 1:
                raise ProviderFailure(retryable=True)
            if mode == "deadline":
                await asyncio.Event().wait()
            if mode == "tool":
                yield completed(call("forbidden"))
                return
            text = (
                "## 文档依据\n根据资料，以下为说明。\n\n餐费上限150元，须持票据。[E1]"
                if mode == "invalid"
                else "## 文档依据\n根据资料，以下为说明。餐费上限150元，须持票据。[E1]"
            )
            if mode == "amount":
                text = text.replace("150", "200")
            elif mode == "delete":
                text = text.replace("，须持票据", "")
            elif mode == "invent":
                text = text.replace("。[E1]", "，无需审批。[E1]")
            yield ProviderDelta(text)
            yield ProviderCompleted(ModelTurn("text", text))
        else:
            ref = "E999" if mode == "unknown" else "E1"
            text = f"## 文档依据\n根据资料，以下为说明。\n\n餐费上限150元，须持票据。[{ref}]"
            yield ProviderCompleted(ModelTurn("text", text))

    executor = RunExecutor(
        repo,
        FakeProvider(answer),
        registry=ToolRegistry((spec(handler),)),
        capabilities=frozenset({"documents.read"}),
    )
    outcome = await executor.execute(uuid4(), data.thread_id, data.run_id)
    repo.append_delta.assert_not_awaited()
    return outcome, repo, stages


def test_v4_repairs_unreferenced_intro_once_without_publishing_invalid_or_private_text(
    model_configuration,
):
    outcome, repo, stages = asyncio.run(execute_case(model_configuration))
    assert outcome == "succeeded"
    assert stages == ["answer", "answer", "repair"]
    assert (
        repo.finish.await_args.kwargs["content"]
        == "## 文档依据\n根据资料，以下为说明。餐费上限150元，须持票据。[E1]"
    )


@pytest.mark.parametrize("mode", ["invalid", "unknown", "tool", "budget"])
def test_repair_cannot_relax_validation_execute_tools_or_exceed_budget(model_configuration, mode):
    outcome, repo, stages = asyncio.run(execute_case(model_configuration, mode))
    assert outcome == "failed"
    assert stages.count("repair") == (0 if mode in {"unknown", "budget"} else 1)
    assert repo.finish.await_args.kwargs["error_code"] == (
        "AGENT_BUDGET_EXCEEDED" if mode == "budget" else "PROVIDER_INVALID_RESPONSE"
    )


def test_v3_keeps_original_strict_failure_and_canonical_hash(model_configuration):
    data = data_with_policy(model_configuration, 3)
    old = data.snapshot.model_dump(mode="json", exclude_none=True)
    assert "repairs" not in old
    expected = hashlib.sha256(
        json.dumps(old, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert ModelSnapshot.model_validate_json(json.dumps(old)).catalog_hash() == expected
    outcome, _, stages = asyncio.run(execute_case(model_configuration, version=3))
    assert outcome == "failed" and stages == ["answer", "answer"]


def test_repair_transport_retry_is_still_within_outer_four_and_does_not_repeat_tool(
    model_configuration,
):
    outcome, repo, stages = asyncio.run(execute_case(model_configuration, "retry"))
    assert outcome == "succeeded" and stages == ["answer", "answer", "repair", "repair"]
    assert repo.record_tool.await_count == 2


def test_repair_wait_is_cancelled_by_original_absolute_deadline(model_configuration):
    config = model_configuration.model_copy(update={"timeout_seconds": 0.1})
    outcome, repo, stages = asyncio.run(execute_case(config, "deadline"))
    assert outcome == "failed" and stages == ["answer", "answer", "repair"]
    assert repo.finish.await_args.kwargs["error_code"] == "AGENT_DEADLINE_EXCEEDED"


@pytest.mark.parametrize("mode", ["amount", "delete", "invent"])
def test_format_repair_must_not_change_amounts_delete_or_invent_body(model_configuration, mode):
    outcome, repo, stages = asyncio.run(execute_case(model_configuration, mode))
    assert outcome == "failed" and stages == ["answer", "answer", "repair"]
    assert repo.finish.await_args.kwargs["error_code"] == "PROVIDER_INVALID_RESPONSE"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_snapshot_digest_has_no_new_policy_fields(model_configuration, version):
    answer = request(model_configuration).snapshot.answer.model_dump(mode="json")
    raw = {"schema_version": version, "answer": answer}
    if version >= 2:
        raw["rag"] = RagPolicy().model_dump(mode="json")
    if version >= 3:
        raw["reasoning"] = ReasoningPolicy().model_dump(mode="json")
    expected = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    snapshot = ModelSnapshot.model_validate_json(json.dumps(raw))
    assert snapshot.repairs is None and snapshot.catalog_hash() == expected
