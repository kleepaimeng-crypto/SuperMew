"""内部角色重评只复用原输入并消耗原预算；不联网。"""

import asyncio
import json
import logging
from dataclasses import replace
from uuid import uuid4

import pytest
from execution_helpers import FakeProvider
from test_deepseek import chunk, request, wire
from test_deepseek import transport as transport

from minimew.execution.chat_provider import ChatProvider
from minimew.execution.diagnostics import Diagnostic, diagnostic_log, emit
from minimew.execution.messages import ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderFailure
from minimew.execution.reasoning import RunModelCaller
from minimew.execution.service import RunBudget
from minimew.rag.reasoning import Grade, ReasoningPolicy, grade_materials

VALID = '{"status":"sufficient","supported":["E1"],"missing":[]}'
DATA = {"question": "餐费多少", "materials": [{"citation_id": "E1", "text": "餐费150元"}]}


async def caller_for(configuration, handler, *, legacy=False, budget=None, timeout=5):
    data = request(configuration)
    policy = ReasoningPolicy(structured_output=None if legacy else "json-repair-v1")
    snapshot = data.snapshot.model_copy(update={"reasoning": policy})
    provider = FakeProvider(handler)
    budget = budget or RunBudget(reasoning=True, model_requests=1)

    async def check():
        pass

    caller = RunModelCaller(
        provider,
        replace(data, snapshot=snapshot),
        budget,
        asyncio.get_running_loop().time() + timeout,
        check,
        policy,
    )
    return caller, provider, budget


@pytest.mark.parametrize(
    "invalid,reason",
    [
        ("```json\n{}\n```", "json_parse_failed"),
        ('{"status":"sufficient","supported":[123],"missing":[]}', "schema_validation_failed"),
        ('{"status":"sufficient","supported":["E99"],"missing":[]}', "unknown_evidence_reference"),
        ('{"status":"sufficient","supported":[],"missing":[]}', "inconsistent_assessment"),
    ],
)
def test_one_reassessment_preserves_input_and_passes_all_validation(
    model_configuration, invalid, reason, caplog
):
    async def scenario():
        calls = []

        async def answer(req):
            calls.append(req)
            assert req.json_output and req.tools == ()
            if len(calls) == 2:
                payload = json.loads(req.messages[0].content)
                assert payload["input"] == DATA
                assert payload["validation_feedback"]["reason"] == reason
            yield ProviderCompleted(ModelTurn("text", invalid if len(calls) == 1 else VALID))

        caller, _, budget = await caller_for(model_configuration, answer)
        result = await caller("grade", DATA, Grade)
        assert result.supported == ("E1",) and budget.internal_requests == 2
        assert calls[0].snapshot is calls[1].snapshot

    asyncio.run(scenario())
    assert "role_response_repair_started" in caplog.text


def test_reassessment_is_bounded_and_does_not_log_invalid_values(model_configuration, caplog):
    secret = "sk-private-document-not-for-logs"

    async def scenario():
        async def answer(req):
            yield ProviderCompleted(
                ModelTurn(
                    "text",
                    json.dumps(
                        {"status": "sufficient", "supported": ["E1"], "missing": [], secret: secret}
                    ),
                )
            )

        caller, provider, budget = await caller_for(model_configuration, answer)
        with pytest.raises(ProviderFailure) as error:
            await caller("grade", DATA, Grade)
        assert error.value.code == "PROVIDER_INVALID_RESPONSE"
        assert error.value.diagnostic.reason == "schema_validation_failed"
        assert error.value.diagnostic.issues == ({"field": "<extra>", "rule": "extra_forbidden"},)
        assert len(provider.requests) == budget.internal_requests == 2

    asyncio.run(scenario())
    assert secret not in caplog.text


def test_legacy_policy_has_no_json_mode_or_new_retry(model_configuration):
    async def scenario():
        async def answer(req):
            assert not req.json_output
            yield ProviderCompleted(ModelTurn("text", "invalid"))

        caller, provider, _ = await caller_for(model_configuration, answer, legacy=True)
        with pytest.raises(ProviderFailure):
            await caller("grade", DATA, Grade)
        assert len(provider.requests) == 1
        assert ReasoningPolicy().model_dump(exclude_none=True).get("structured_output") is None

    asyncio.run(scenario())


def test_parallel_role_corrections_keep_each_subquestion_and_evidence(model_configuration):
    async def scenario():
        async def answer(req):
            payload = json.loads(req.messages[0].content)
            if "input" not in payload:
                await asyncio.sleep(0)
                text = "invalid"
            else:
                data = payload["input"]
                await asyncio.sleep(0)
                text = json.dumps(
                    {
                        "status": "sufficient",
                        "supported": [data["materials"][0]["citation_id"]],
                        "missing": [],
                    }
                )
            yield ProviderCompleted(ModelTurn("text", text))

        caller, _, budget = await caller_for(model_configuration, answer)
        second = {"question": "住宿多少", "materials": [{"citation_id": "E2", "text": "住宿350元"}]}
        first, other = await asyncio.gather(
            caller("grade", DATA, Grade), caller("grade", second, Grade)
        )
        assert first.supported == ("E1",) and other.supported == ("E2",)
        assert budget.internal_requests == 4 and caller.slots._value == 2

    asyncio.run(scenario())


def test_reserved_budget_does_not_turn_real_failure_into_insufficient(model_configuration):
    async def scenario():
        async def answer(req):
            yield ProviderCompleted(ModelTurn("text", "invalid"))

        budget = RunBudget(reasoning=True, finalization=True, model_requests=1, internal_requests=8)
        caller, provider, _ = await caller_for(model_configuration, answer, budget=budget)

        async def call(*args):
            return await caller(*args)

        from types import SimpleNamespace

        with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_RESPONSE"):
            await grade_materials(call, "q", SimpleNamespace(matches=()))
        assert len(provider.requests) == 1 and budget.internal_requests == 9

    asyncio.run(scenario())


def test_transport_and_correction_share_one_owner_and_original_budget(model_configuration):
    async def scenario():
        attempts = 0

        async def answer(req):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ProviderFailure(retryable=True)
            yield ProviderCompleted(ModelTurn("text", "invalid" if attempts == 2 else VALID))

        caller, provider, budget = await caller_for(model_configuration, answer)
        assert (await caller("grade", DATA, Grade)).status == "sufficient"
        assert len(provider.requests) == budget.internal_requests == 3

    asyncio.run(scenario())


def test_deadline_during_reassessment_cancels_stream(model_configuration):
    async def scenario():
        calls, closed = 0, []

        async def answer(req):
            nonlocal calls
            calls += 1
            try:
                if calls == 1:
                    yield ProviderCompleted(ModelTurn("text", "invalid"))
                else:
                    await asyncio.Event().wait()
            finally:
                closed.append(calls)

        caller, _, _ = await caller_for(model_configuration, answer, timeout=0.1)
        with pytest.raises(TimeoutError):
            await caller("grade", DATA, Grade)
        assert calls == len(closed) == 2 and caller.slots._value == 2

    asyncio.run(scenario())


def test_json_mode_is_sent_to_real_sdk_only_for_internal_roles(transport, model_configuration):
    import httpx2
    from pydantic import SecretStr

    async def response(req):
        payload = json.loads(req.content)
        assert payload["response_format"] == {"type": "json_object"}
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk(VALID), chunk(finish="stop")),
        )

    transport(response)
    data = replace(request(model_configuration), purpose="grade", json_output=True)

    async def scenario():
        values = [v async for v in ChatProvider(SecretStr("test-key")).stream(data)]
        assert values[-1].turn.content == VALID

    asyncio.run(scenario())
    with pytest.raises(ValueError):
        replace(data, purpose="answer")


@pytest.mark.parametrize(
    "finish,content,reason",
    [("length", "{", "output_truncated"), ("stop", " ", "empty_model_output")],
)
def test_provider_classifies_truncated_and_blank_responses(
    transport, model_configuration, finish, content, reason
):
    import httpx2
    from pydantic import SecretStr

    async def response(req):
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=wire(chunk(content), chunk(finish=finish)),
        )

    transport(response)

    async def scenario():
        with pytest.raises(ProviderFailure) as error:
            async for _ in ChatProvider(SecretStr("test-key")).stream(
                replace(request(model_configuration), purpose="grade", json_output=True)
            ):
                pass
        assert error.value.diagnostic.reason == reason

    asyncio.run(scenario())


def test_diagnostics_survive_reopen_and_exclude_uncontrolled_logs(tmp_path):
    logger = logging.getLogger("minimew.role-test")
    run = uuid4()
    for _ in range(2):
        with diagnostic_log(tmp_path):
            logger.warning("not-a-diagnostic-private-content")
            emit(
                logger,
                "role_failed",
                run,
                "PROVIDER_INVALID_RESPONSE",
                Diagnostic(
                    "grade",
                    "schema_validation_failed",
                    issues=({"field": "missing", "rule": "too_long"},),
                ),
            )
    files = list(tmp_path.glob("execution-*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 and all(r["run_id"] == str(run) for r in records)
    assert records[0]["issues"][0]["field"] == "missing"
    assert "private-content" not in files[0].read_text(encoding="utf-8")
