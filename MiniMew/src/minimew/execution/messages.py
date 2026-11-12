"""模型内部消息与工具描述；JSON以不可变文本保存，不运行任何工具。"""

import json
import re
from dataclasses import dataclass
from typing import Literal

MAX_INPUT_CHARS = 60000
MAX_MESSAGES = 128
MAX_TOOLS = 16
MAX_TOOL_CALLS = 8
MAX_ARGUMENT_CHARS = 16000
MAX_OUTPUT_CHARS = 100000


def valid_text(value: object, maximum: int, *, nonblank: bool = False) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= maximum or "\x00" in value:
        return False
    if nonblank and not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON number")


def json_object(value: str, maximum=MAX_ARGUMENT_CHARS) -> dict:
    if not valid_text(value, maximum, nonblank=True):
        raise ValueError("Invalid JSON text")
    try:
        result = json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
        # 1e999也会被Python解析为inf，重新编码时拒绝。
        encoded = json.dumps(result, allow_nan=False, ensure_ascii=False)
        encoded.encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Invalid JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    return result


def valid_name(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value) is not None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters_json: str

    def __post_init__(self):
        if not valid_name(self.name) or not valid_text(self.description, 4096, nonblank=True):
            raise ValueError("Invalid tool definition")
        schema = json_object(self.parameters_json)
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("Tool parameters must declare object properties")

    def wire(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json_object(self.parameters_json),
            },
        }


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str

    def __post_init__(self):
        if not valid_text(self.id, 128, nonblank=True) or not valid_name(self.name):
            raise ValueError("Invalid tool call identity")
        json_object(self.arguments_json)


@dataclass(frozen=True)
class InputMessage:
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.tool_calls, tuple) or any(
            not isinstance(call, ToolCall) for call in self.tool_calls
        ):
            raise ValueError("Immutable tool calls required")
        if self.role not in ("user", "assistant", "tool"):
            raise ValueError("Invalid message role")
        if self.tool_calls:
            if (
                self.role != "assistant"
                or self.tool_call_id is not None
                or len(self.tool_calls) > MAX_TOOL_CALLS
                or len({call.id for call in self.tool_calls}) != len(self.tool_calls)
            ):
                raise ValueError("Invalid assistant tool calls")
            if self.content != "" and not valid_text(self.content, MAX_OUTPUT_CHARS):
                raise ValueError("Invalid assistant content")
        elif not valid_text(self.content, MAX_OUTPUT_CHARS, nonblank=True):
            raise ValueError("Invalid message content")
        if self.role == "tool":
            if not valid_text(self.tool_call_id, 128, nonblank=True):
                raise ValueError("Tool result requires call identity")
        elif self.tool_call_id is not None:
            raise ValueError("Only tool results carry tool_call_id")


def validate_messages(messages, tools):
    if (
        not isinstance(messages, tuple)
        or not 1 <= len(messages) <= MAX_MESSAGES
        or not isinstance(tools, tuple)
        or len(tools) > MAX_TOOLS
        or any(not isinstance(tool, ToolDefinition) for tool in tools)
    ):
        raise ValueError("Invalid model request")
    names = {tool.name for tool in tools}
    if len(names) != len(tools):
        raise ValueError("Duplicate tool definition")
    size = sum(len(tool.name) + len(tool.description) + len(tool.parameters_json) for tool in tools)
    pending, used = set(), set()
    if any(not isinstance(message, InputMessage) for message in messages):
        raise ValueError("Invalid message")
    if messages[0].role != "user":
        raise ValueError("Conversation must start with user")
    for message in messages:
        size += len(message.content)
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise ValueError("Unexpected or duplicate tool result")
            pending.remove(message.tool_call_id)
            size += len(message.tool_call_id)
        else:
            if pending:
                raise ValueError("Tool results are incomplete")
            for call in message.tool_calls:
                if call.id in used or call.name not in names:
                    raise ValueError("Duplicate or undeclared tool call")
                pending.add(call.id)
                used.add(call.id)
                size += len(call.id) + len(call.name) + len(call.arguments_json)
    if pending or messages[-1].role == "assistant" or size > MAX_INPUT_CHARS:
        raise ValueError("Incomplete or oversized model context")


@dataclass(frozen=True)
class ModelTurn:
    """一回合完成；工具请求的完成不等于业务Run完成。"""

    kind: Literal["text", "tool_calls"]
    content: str
    tool_calls: tuple[ToolCall, ...] = ()


def validate_turn(turn):
    if not isinstance(turn, ModelTurn) or turn.kind not in ("text", "tool_calls"):
        raise ValueError("Invalid model turn")
    message = InputMessage("assistant", turn.content, turn.tool_calls)
    if (turn.kind == "tool_calls") != bool(message.tool_calls):
        raise ValueError("Model finish reason and payload disagree")
    size = len(turn.content) + sum(
        len(call.id) + len(call.name) + len(call.arguments_json) for call in turn.tool_calls
    )
    if size > MAX_OUTPUT_CHARS:
        raise ValueError("Oversized model turn")
