"""证据身份、预算与混合答案的来源/结构校验；不联网。"""

import asyncio
import hashlib
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from test_deepseek import request
from test_execution_retry import Repository
from test_knowledge import invoke, setup_service

from minimew.evidence import (
    ANSWER_INSTRUCTIONS,
    DOCUMENT_HEADING,
    SUPPLEMENT_HEADING,
    AnswerMaterials,
    Evidence,
    EvidenceLedger,
    EvidenceLimit,
    EvidenceSource,
    parse_answer,
    validate_materials,
)
from minimew.execution.messages import ModelTurn, ToolCall
from minimew.execution.provider import ProviderCompleted
from minimew.execution.service import RunExecutor
from minimew.knowledge import NAME


def source(text="餐费150元。"):
    return EvidenceSource(
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        version_id=uuid4(),
        chunk_id=uuid4(),
        section_id=uuid4(),
        title_path=("制度", "餐费"),
        start=10,
        end=10 + len(text),
        start_line=2,
        end_line=2,
        text=text,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def evidence(text="餐费150元。", citation_id="E1"):
    return Evidence(**source(text).model_dump(), citation_id=citation_id)


def mixed(content="餐费上限150元。", citation_id="E1"):
    return f"{DOCUMENT_HEADING}\n{content}[{citation_id}]\n\n{SUPPLEMENT_HEADING}\n建议保留凭证。"


def test_unicode_offsets_and_exact_material_roundtrip():
    first, second = evidence(), evidence("住宿500元。", "E2")
    content = (
        f"{DOCUMENT_HEADING}\n餐费150元[E1]。\n\n住宿500元[E2]，另见[E1]。\n\n"
        f"{SUPPLEMENT_HEADING}\n一般建议。"
    )
    answer = parse_answer(content, (first, second))
    assert [c.citation_id for c in answer.materials.citations] == ["E1", "E2", "E1"]
    assert [content[c.start : c.end] for c in answer.materials.citations] == [
        "[E1]",
        "[E2]",
        "[E1]",
    ]
    assert [s.kind for s in answer.materials.sections] == ["document", "supplement"]
    parsed = AnswerMaterials.model_validate_json(answer.materials.model_dump_json())
    validate_materials(content, parsed)
    with pytest.raises(ValueError):
        validate_materials("前缀" + content, parsed)


@pytest.mark.parametrize(
    "content",
    [
        "无分区[E1]",
        f"前言\n{DOCUMENT_HEADING}\n有据[E1]",
        f"{DOCUMENT_HEADING}\n无引用",
        f"{DOCUMENT_HEADING}\n有据[E99]",
        f"{DOCUMENT_HEADING}\n有据[E0]",
        f"{DOCUMENT_HEADING}\n有据[E1",
        f"{DOCUMENT_HEADING}\n",
        f"{SUPPLEMENT_HEADING}\n补充[E1]",
        f"{SUPPLEMENT_HEADING}\n补充\n{DOCUMENT_HEADING}\n有据[E1]",
        f"{DOCUMENT_HEADING}\n有据[E1]\n{DOCUMENT_HEADING}\n重复[E1]",
        f"{DOCUMENT_HEADING}\n有据[E1]\n\n另一段无引用",
        f"{SUPPLEMENT_HEADING}\n",
    ],
)
def test_invalid_answer_is_not_silently_repaired(content):
    with pytest.raises(ValueError):
        parse_answer(content, (evidence(),))


def test_supplement_only_and_omitted_unreferenced_evidence():
    content = f"{SUPPLEMENT_HEADING}\n本次没有足够资料。以下是一般建议。"
    answer = parse_answer(content, ())
    assert answer.materials.citations == answer.materials.evidence == ()
    assert len(answer.materials.sections) == 1
    used, unused = evidence(), evidence("其他资料", "E2")
    assert parse_answer(mixed(), (used, unused)).materials.evidence == (used,)


def test_ledger_stable_ids_across_searches_and_distinct_versions():
    ledger = EvidenceLedger(uuid4(), uuid4(), uuid4())
    item = source()
    first = ledger.propose(item, ())
    ledger.commit((first,))
    assert ledger.propose(item, ()) is first
    changed = item.model_copy(update={"version_id": uuid4()})
    assert ledger.propose(changed, ()).citation_id == "E2"
    other = EvidenceLedger(uuid4(), uuid4(), uuid4())
    assert other.propose(source("其他用户原文"), ()).citation_id == "E1"
    assert other.items == () and ledger.items == (first,)


def test_cumulative_evidence_budget_does_not_mutate_on_proposal_failure():
    ledger = EvidenceLedger(uuid4(), uuid4(), uuid4())
    for _ in range(2):
        item = ledger.propose(source("原" * 9000), ())
        ledger.commit((item,))
    before = ledger.items
    with pytest.raises(EvidenceLimit):
        ledger.propose(source("原" * 9000), ())
    assert ledger.items == before


def test_tool_registers_only_budget_selected_sources_and_reuses_ids(monkeypatch):
    _, registry, context, _, _, _, _ = setup_service(monkeypatch)

    async def scenario():
        first = json.loads(await invoke(registry, context))
        second = json.loads(await invoke(registry, context))
        assert [m["citation_id"] for m in first["matches"]] == ["E1", "E2", "E3"]
        assert first == second and len(context.evidence.items) == 3
        assert context.evidence.queried

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [False, True])
def test_executor_passes_run_owned_materials_and_has_no_correction_round(
    monkeypatch, model_configuration, invalid
):
    _, registry, context, _, _, _, _ = setup_service(monkeypatch)

    async def scenario():
        repo = Repository(request(model_configuration))

        async def respond(data):
            if data.messages[-1].role != "tool":
                yield ProviderCompleted(
                    ModelTurn("tool_calls", "", (ToolCall("c", NAME, '{"query":"餐费"}'),))
                )
            else:
                assert data.instructions == ANSWER_INSTRUCTIONS
                value = json.loads(data.messages[-1].content)
                assert value["matches"][0]["citation_id"] == "E1"
                yield ProviderCompleted(
                    ModelTurn("text", mixed(citation_id="E99" if invalid else "E1"))
                )

        provider = FakeProvider(respond)
        result = await RunExecutor(
            repo, provider, registry=registry, capabilities=context.capabilities
        ).execute(context.user_id, repo.data.thread_id, repo.data.run_id)
        assert result == ("failed" if invalid else "succeeded")
        assert len(provider.requests) == 2
        saved = repo.finish.await_args.kwargs
        assert saved["error_code"] == ("PROVIDER_INVALID_RESPONSE" if invalid else None)
        if invalid:
            assert "materials" not in saved
        else:
            assert saved["materials"].evidence[0].citation_id == "E1"
        assert context.evidence.items == ()  # 注册用的其他上下文没有被执行器重用

    asyncio.run(scenario())


def test_server_instruction_size_is_part_of_context_budget(model_configuration):
    data = request(model_configuration)
    with pytest.raises(ValueError):
        replace(data, instructions="x" * 4097)
    assert replace(data, instructions=ANSWER_INSTRUCTIONS).messages == data.messages


@pytest.mark.parametrize("empty", ["catalog", "matches"])
def test_healthy_empty_knowledge_keeps_explicit_supplement(monkeypatch, model_configuration, empty):
    _, registry, context, _, scoped, search, _ = setup_service(monkeypatch)
    if empty == "catalog":
        scoped.return_value = replace(scoped.return_value, targets=())
    else:
        search.return_value = []

    async def scenario():
        repo = Repository(request(model_configuration))

        async def respond(data):
            if data.messages[-1].role != "tool":
                yield ProviderCompleted(
                    ModelTurn("tool_calls", "", (ToolCall("c", NAME, '{"query":"餐费"}'),))
                )
            else:
                assert json.loads(data.messages[-1].content)["matches"] == []
                yield ProviderCompleted(
                    ModelTurn("text", f"{SUPPLEMENT_HEADING}\n本次没有文档依据。仅能给出一般建议。")
                )

        assert (
            await RunExecutor(
                repo, FakeProvider(respond), registry=registry, capabilities=context.capabilities
            ).execute(context.user_id, repo.data.thread_id, repo.data.run_id)
            == "succeeded"
        )
        materials = repo.finish.await_args.kwargs["materials"]
        assert materials.evidence == materials.citations == ()
        assert materials.sections[0].kind == "supplement"

    asyncio.run(scenario())


def test_same_executor_concurrent_runs_have_independent_evidence(monkeypatch, model_configuration):
    _, registry, context, _, _, _, _ = setup_service(monkeypatch)

    async def scenario():
        data = [request(model_configuration), request(model_configuration)]
        repo = Repository(data[0])
        repo.claim = AsyncMock(side_effect=lambda user, thread, rid: rid)
        repo.request = AsyncMock(side_effect=lambda rid: next(d for d in data if d.run_id == rid))

        async def respond(current):
            if current.messages[-1].role != "tool":
                await asyncio.sleep(0)
                yield ProviderCompleted(
                    ModelTurn("tool_calls", "", (ToolCall("c", NAME, '{"query":"餐费"}'),))
                )
            else:
                assert json.loads(current.messages[-1].content)["matches"][0]["citation_id"] == "E1"
                yield ProviderCompleted(ModelTurn("text", mixed(str(current.run_id))))

        executor = RunExecutor(
            repo, FakeProvider(respond), registry=registry, capabilities=context.capabilities
        )
        assert await asyncio.gather(
            *(executor.execute(context.user_id, d.thread_id, d.run_id) for d in data)
        ) == ["succeeded", "succeeded"]
        saved = repo.finish.await_args_list
        assert len(saved) == 2
        assert {call.args[0] for call in saved} == {d.run_id for d in data}
        assert all(len(call.kwargs["materials"].evidence) == 1 for call in saved)
        assert saved[0].kwargs["materials"] is not saved[1].kwargs["materials"]

    asyncio.run(scenario())
