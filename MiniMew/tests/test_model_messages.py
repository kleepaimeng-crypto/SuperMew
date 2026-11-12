"""模型内部协议：不可变描述、完整JSON和调用/结果关联。"""

from dataclasses import FrozenInstanceError, replace

import pytest
from test_deepseek import request

from minimew.execution.messages import (
    MAX_INPUT_CHARS,
    InputMessage,
    ModelTurn,
    ToolCall,
    ToolDefinition,
    json_object,
    validate_turn,
)


def search_tool():
    return ToolDefinition(
        "knowledge_search",
        "查询文档",
        '{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}',
    )


def test_definitions_calls_and_wire_are_independent_immutable_values():
    tool = search_tool()
    first = tool.wire()
    first["function"]["parameters"]["properties"].clear()
    assert "query" in tool.wire()["function"]["parameters"]["properties"]
    with pytest.raises(FrozenInstanceError):
        tool.name = "other"
    call = ToolCall("call_meal", tool.name, '{"query":"餐费"}')
    with pytest.raises(FrozenInstanceError):
        call.arguments_json = "{}"


@pytest.mark.parametrize(
    "value",
    [
        '{"query":',
        "{}{}",
        "[]",
        '{"query":1,"query":2}',
        '{"nested":{"a":1,"a":2}}',
        '{"query":NaN}',
        '{"query":Infinity}',
        '{"query":1e999}',
        r'{"query":"\ud800"}',
        '{"query":"' + "x" * 16000 + '"}',
    ],
    ids=[
        "partial",
        "trailing",
        "array",
        "duplicate",
        "nested-duplicate",
        "nan",
        "inf",
        "overflow",
        "surrogate",
        "budget",
    ],
)
def test_incomplete_or_ambiguous_arguments_are_rejected(value):
    with pytest.raises(ValueError):
        ToolCall("call_1", "knowledge_search", value)


def paired_messages():
    calls = (
        ToolCall("call_meal", "knowledge_search", '{"query":"餐费"}'),
        ToolCall("call_hotel", "knowledge_search", '{"query":"住宿"}'),
    )
    return (
        InputMessage("user", "差旅标准？"),
        InputMessage("assistant", "", calls),
        InputMessage("tool", "住宿证据", tool_call_id="call_hotel"),
        InputMessage("tool", "餐费证据", tool_call_id="call_meal"),
    )


def test_same_name_calls_pair_by_id_even_if_results_are_reordered(model_configuration):
    data = replace(request(model_configuration), tools=(search_tool(),), messages=paired_messages())
    assert [message.tool_call_id for message in data.messages[-2:]] == ["call_hotel", "call_meal"]
    assert json_object(data.messages[1].tool_calls[0].arguments_json) == {"query": "餐费"}


@pytest.mark.parametrize(
    "mode", ["missing", "duplicate", "wrong", "interrupted", "undeclared", "reuse"]
)
def test_tool_results_cannot_be_missing_mismatched_or_reused(model_configuration, mode):
    messages = paired_messages()
    tools = (search_tool(),)
    if mode == "missing":
        messages = messages[:-1]
    elif mode == "duplicate":
        messages = (*messages, messages[-1])
    elif mode == "wrong":
        messages = (*messages[:-1], InputMessage("tool", "data", tool_call_id="wrong"))
    elif mode == "interrupted":
        messages = (*messages[:2], InputMessage("user", "next"), *messages[2:])
    elif mode == "undeclared":
        tools = ()
    else:
        messages = (*messages, messages[1], *messages[2:])
    with pytest.raises(ValueError):
        replace(request(model_configuration), tools=tools, messages=messages)


def test_request_budget_includes_tool_data_and_requires_immutable_collections(model_configuration):
    data = request(model_configuration)
    with pytest.raises(ValueError):
        replace(data, messages=[InputMessage("user", "q")])
    with pytest.raises(ValueError):
        replace(data, tools=(search_tool(), search_tool()))
    with pytest.raises(ValueError):
        replace(
            data, messages=(InputMessage("user", "x" * MAX_INPUT_CHARS),), tools=(search_tool(),)
        )
    with pytest.raises(ValueError):
        InputMessage("user", "q", tool_calls=(ToolCall("c", "search", "{}"),))
    with pytest.raises(ValueError):
        InputMessage("tool", "data")


@pytest.mark.parametrize(
    "turn",
    [
        ModelTurn("text", " "),
        ModelTurn("tool_calls", "text"),
        ModelTurn("text", "", (ToolCall("c", "search", "{}"),)),
    ],
)
def test_turn_finish_kind_must_match_payload(turn):
    with pytest.raises(ValueError):
        validate_turn(turn)
