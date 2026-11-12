"""工具注册/参数/授权边界；只有受控测试handler。"""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from pydantic import Field
from test_execution import event as event

from minimew.execution.messages import ToolCall
from minimew.execution.tools import (
    Guardrail,
    ToolContext,
    ToolFailure,
    ToolModel,
    ToolRegistry,
    ToolSpec,
)


class Query(ToolModel):
    query: str = Field(min_length=1, max_length=200)


class Result(ToolModel):
    text: str = Field(min_length=1)


def context():
    return ToolContext(uuid4(), uuid4(), uuid4(), frozenset({"documents.read"}))


def spec(handler, *, authorize=lambda context, arguments: True):
    return ToolSpec("search", "受控查询", Query, Result, "documents.read", authorize, handler)


def call(identity="call_1", arguments='{"query":"餐费"}', name="search"):
    return ToolCall(identity, name, arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        '{"query":123}',
        '{"query":true}',
        '{"query":null}',
        '{"query":[]}',
        '{"query":""}',
        "{}",
        '{"query":"q","user_id":"forged"}',
    ],
)
def test_strict_parameters_fail_before_authorization_or_handler(arguments):
    def forbidden(*args):
        raise AssertionError("Invalid parameters must not reach authorization or handler")

    registry = ToolRegistry((spec(forbidden, authorize=forbidden),))
    with pytest.raises(ToolFailure, match="TOOL_INVALID_ARGUMENTS"):
        registry.prepare((call(arguments=arguments),), context())


def test_catalog_schema_immutable_and_visibility_does_not_grant_resource_access():
    ctx = context()
    registry = ToolRegistry((spec(lambda *args: None, authorize=lambda c, a: False),))
    assert (
        registry.visible(ctx)[0].wire()["function"]["parameters"]["additionalProperties"] is False
    )
    assert registry.visible(replace(ctx, capabilities=frozenset())) == ()
    with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
        registry.prepare((call(),), ctx)
    with pytest.raises(FrozenInstanceError):
        ctx.user_id = uuid4()
    with pytest.raises(ValueError):
        replace(ctx, capabilities={"documents.read"})
    with pytest.raises(ValueError):
        ToolRegistry((registry.specs[0], registry.specs[0]))


@pytest.mark.parametrize(
    "mode,code",
    [
        ("unknown", "TOOL_NOT_AVAILABLE"),
        ("capability", "TOOL_PERMISSION_DENIED"),
        ("resource", "TOOL_PERMISSION_DENIED"),
        ("fault", "TOOL_EXECUTION_FAILED"),
    ],
)
def test_guardrail_distinguishes_rejection_and_fault_without_exposing_details(mode, code):
    ctx = context()

    def authorize(c, a):
        assert c is ctx
        if mode == "fault":
            raise RuntimeError("private authorization backend")
        return mode != "resource"

    registry = ToolRegistry((spec(lambda *args: None, authorize=authorize),))
    if mode == "capability":
        ctx = replace(ctx, capabilities=frozenset())
    with pytest.raises(ToolFailure) as error:
        registry.prepare((call(name="missing" if mode == "unknown" else "search"),), ctx)
    assert str(error.value) == code


def test_guardrail_rechecks_before_invoking_prepared_call():
    import asyncio

    allowed = True
    ctx = context()

    async def forbidden(c, a):
        raise AssertionError("Revoked operation must not execute")

    registry = ToolRegistry((spec(forbidden, authorize=lambda c, a: allowed),))
    prepared = registry.prepare((call(),), ctx)[0]
    allowed = False
    with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
        asyncio.run(registry.invoke(prepared, ctx))


@pytest.mark.parametrize(
    "mode,code",
    [
        ("exception", "TOOL_EXECUTION_FAILED"),
        ("type", "TOOL_INVALID_RESULT"),
        ("extra", "TOOL_INVALID_RESULT"),
        ("large", "TOOL_INVALID_RESULT"),
    ],
)
def test_handler_and_result_failures_are_bounded_typed_and_not_retried(mode, code):
    import asyncio

    calls = []

    async def handler(c, a):
        calls.append((c, a))
        if mode == "exception":
            raise RuntimeError("private-tool-credential")
        return (
            {"text": 123}
            if mode == "type"
            else {"text": "q", "secret": "x"}
            if mode == "extra"
            else Result(text="x" * 12001)
        )

    registry, ctx = ToolRegistry((spec(handler),)), context()
    prepared = registry.prepare((call(),), ctx)[0]
    with pytest.raises(ToolFailure) as error:
        asyncio.run(registry.invoke(prepared, ctx))
    assert str(error.value) == code and len(calls) == 1


def test_parameter_models_cannot_disable_strict_contract():
    class Loose(Query):
        model_config = {"strict": False}

    with pytest.raises(ValueError, match="Strict tool models"):
        replace(spec(lambda *args: None), parameters=Loose)
    with pytest.raises(ToolFailure, match="TOOL_PERMISSION_DENIED"):
        Guardrail.check(
            replace(context(), capabilities=frozenset()), spec(lambda *args: None), Query(query="q")
        )


def test_guardrail_returns_policy_identified_decision():
    ctx = context()
    tool = spec(lambda *args: None)
    decision = Guardrail.check(ctx, tool, Query(query="q"))
    assert decision.outcome == "ALLOW"
    assert decision.policy_id == "tool-capability-and-resource-v1"
    denied = Guardrail.decide(replace(ctx, capabilities=frozenset()), tool, Query(query="q"))
    assert denied.outcome == "DENY" and denied.policy_id == decision.policy_id


@pytest.mark.parametrize(
    "code",
    [
        "TOOL_NOT_AVAILABLE",
        "TOOL_INVALID_ARGUMENTS",
        "TOOL_PERMISSION_DENIED",
        "TOOL_EXECUTION_FAILED",
        "TOOL_INVALID_RESULT",
        "AGENT_BUDGET_EXCEEDED",
        "AGENT_DEADLINE_EXCEEDED",
    ],
)
def test_tool_failure_codes_round_trip_formal_event_contract(event, code):
    import json

    from minimew.generated.run_event_v1 import EVENT_ADAPTER

    event["data"]["error_code"] = code
    parsed = EVENT_ADAPTER.validate_json(json.dumps(event))
    assert parsed.data.error_code == code
    assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(parsed)) == parsed
