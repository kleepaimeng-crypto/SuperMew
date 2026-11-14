import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from minimew.execution.messages import InputMessage, ModelTurn
from minimew.execution.provider import ProviderCompleted, ProviderFailure, ProviderRequest
from minimew.execution.reasoning import RunModelCaller
from minimew.execution.service import RunBudget
from minimew.execution.tools import ToolFailure
from minimew.model_control import ModelSnapshot, ProfileSnapshot
from minimew.rag.protocol import RagPolicy
from minimew.rag.reasoning import Grade, ReasoningPolicy, grade_materials


def test_shared_budget_reserves_answer_and_counts_retrieval_across_calls():
    b = RunBudget(reasoning=True, model_requests=1)
    for _ in range(10):
        b.take_internal()
    with pytest.raises(ToolFailure):
        b.take_internal()
    b.require_model()
    b.model_requests += 1
    with pytest.raises(ToolFailure):
        b.require_model()
    for _ in range(8):
        b.take_retrieval()
    with pytest.raises(ToolFailure):
        b.take_retrieval()
    assert RunBudget().internal_requests == 0


def test_v3_policy_is_frozen_and_v2_hash_stable(model_configuration):
    base = ModelSnapshot(
        schema_version=2,
        answer=ProfileSnapshot(
            profile_id=uuid4(), profile_version=1, configuration=model_configuration
        ),
        rag=RagPolicy(),
    )
    import hashlib

    old = base.model_dump(mode="json", exclude_none=True)
    assert (
        base.catalog_hash()
        == hashlib.sha256(
            json.dumps(old, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    with pytest.raises(ValidationError):
        ReasoningPolicy(model_limit=24)
    assert (
        ModelSnapshot(
            schema_version=3, answer=base.answer, rag=base.rag, reasoning=ReasoningPolicy()
        ).catalog_hash()
        != base.catalog_hash()
    )


def test_caller_reuses_provider_without_public_body_and_retries_share_budget(model_configuration):
    async def check():
        pass

    class Provider:
        calls = []

        async def stream(self, request):
            self.calls.append(request)
            assert request.purpose == "grade" and not request.tools
            yield ProviderCompleted(
                ModelTurn("text", '{"status":"sufficient","supported":["E1"],"missing":[]}')
            )

    async def scenario():
        snapshot = ModelSnapshot(
            schema_version=3,
            answer=ProfileSnapshot(
                profile_id=uuid4(), profile_version=1, configuration=model_configuration
            ),
            rag=RagPolicy(),
            reasoning=ReasoningPolicy(),
        )
        request = ProviderRequest(uuid4(), uuid4(), snapshot, (InputMessage("user", "question"),))
        budget = RunBudget(reasoning=True, model_requests=1)
        provider = Provider()
        caller = RunModelCaller(
            provider, request, budget, time.monotonic() + 5, check, snapshot.reasoning
        )
        result = await caller("grade", {"question": "q", "materials": []}, Grade)
        assert result.supported == ("E1",) and budget.internal_requests == 1
        assert (
            snapshot.answer.configuration.max_output_tokens == model_configuration.max_output_tokens
        )

    asyncio.run(scenario())


def test_grade_cannot_reference_material_not_present_in_draft():
    async def caller(*args):
        return Grade(status="sufficient", supported=("E9",), missing=())

    with pytest.raises(ProviderFailure):
        asyncio.run(grade_materials(caller, "q", SimpleNamespace(matches=())))


@pytest.mark.parametrize("mode", ["retry", "invalid", "deadline", "concurrency", "cancel"])
def test_internal_attempts_deadline_json_and_cancellation_release(model_configuration, mode):
    async def scenario():
        snapshot = ModelSnapshot(
            schema_version=3,
            answer=ProfileSnapshot(
                profile_id=uuid4(), profile_version=1, configuration=model_configuration
            ),
            rag=RagPolicy(),
            reasoning=ReasoningPolicy(),
        )
        request = ProviderRequest(uuid4(), uuid4(), snapshot, (InputMessage("user", "q"),))
        budget = RunBudget(reasoning=True, model_requests=1)
        active, maximum, attempts, closed = 0, 0, 0, 0
        entered = asyncio.Event()

        class Provider:
            async def stream(self, request):
                nonlocal active, maximum, attempts, closed
                active += 1
                maximum = max(maximum, active)
                attempts += 1
                entered.set()
                try:
                    if mode == "retry" and attempts == 1:
                        raise ProviderFailure(retryable=True)
                    if mode == "deadline":
                        raise ProviderFailure(retryable=True, retry_after=60)
                    if mode == "cancel":
                        await asyncio.Event().wait()
                    await asyncio.sleep(0.01)
                    text = '{"query":"a","query":"b"}' if mode == "invalid" else '{"query":"规则"}'
                    yield ProviderCompleted(ModelTurn("text", text))
                finally:
                    closed += 1
                    active -= 1

        async def check():
            pass

        from minimew.rag.reasoning import Rewrite

        caller = RunModelCaller(
            Provider(),
            request,
            budget,
            asyncio.get_running_loop().time() + 5,
            check,
            snapshot.reasoning,
        )
        if mode == "cancel":
            task = asyncio.create_task(caller("rewrite", {}, Rewrite))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert caller.slots._value == 2
        elif mode in {"invalid", "deadline"}:
            with pytest.raises(ProviderFailure if mode == "invalid" else ToolFailure):
                await caller("rewrite", {}, Rewrite)
            assert attempts == 1
        elif mode == "concurrency":
            await asyncio.gather(*(caller("rewrite", {}, Rewrite) for _ in range(4)))
            assert maximum == 2
        else:
            await caller("rewrite", {}, Rewrite)
            assert attempts == 2
        assert attempts == closed == budget.internal_requests and active == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("signal", ["cancel", "owner", "storage"])
def test_tool_boundary_preserves_execution_control(monkeypatch, signal):
    from dataclasses import replace

    from test_knowledge import invoke, setup_service

    from minimew.execution.control import (
        CancellationRequested,
        ExecutionStorageUnavailable,
        OwnershipLost,
    )

    _, registry, context, _, _, _, _ = setup_service(monkeypatch)
    error = {
        "cancel": CancellationRequested,
        "owner": OwnershipLost,
        "storage": ExecutionStorageUnavailable,
    }[signal]

    async def caller(*args):
        raise error()

    context = replace(
        context,
        reasoning=ReasoningPolicy(),
        run_budget=RunBudget(reasoning=True),
        model_caller=caller,
    )
    with pytest.raises(error):
        asyncio.run(invoke(registry, context))
    assert context.evidence.items == ()
