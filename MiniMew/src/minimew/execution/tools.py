"""服务端工具目录、严格契约和执行前授权；无默认业务工具或网络重试。"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from minimew.execution.messages import ToolCall, ToolDefinition, valid_text
from minimew.generated.run_event_v1 import ErrorCode

MAX_TOOL_RESULT_CHARS = 12000
TOOL_ERROR_CODES = frozenset(
    {
        "TOOL_NOT_AVAILABLE",
        "TOOL_INVALID_ARGUMENTS",
        "TOOL_PERMISSION_DENIED",
        "TOOL_EXECUTION_FAILED",
        "TOOL_INVALID_RESULT",
        "AGENT_BUDGET_EXCEEDED",
        "AGENT_DEADLINE_EXCEEDED",
        "KNOWLEDGE_ENCODING_FAILED",
        "KNOWLEDGE_RETRIEVAL_FAILED",
        "KNOWLEDGE_SCOPE_INVALID",
        "KNOWLEDGE_RESULT_INVALID",
        "KNOWLEDGE_QUERY_TOO_LONG",
    }
)


class ToolFailure(Exception):
    def __init__(self, code: ErrorCode):
        if code not in TOOL_ERROR_CODES:
            raise ValueError("Unsupported tool failure")
        self.code = code
        super().__init__(code)


class ToolModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        validate_default=True,
    )


@dataclass(frozen=True)
class ToolContext:
    user_id: UUID
    thread_id: UUID
    run_id: UUID
    capabilities: frozenset[str]
    tool_sessions: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self):
        if not all(
            isinstance(value, UUID) for value in (self.user_id, self.thread_id, self.run_id)
        ):
            raise ValueError("Server-owned resource identity required")
        if not isinstance(self.capabilities, frozenset) or any(
            not valid_text(value, 128, nonblank=True) for value in self.capabilities
        ):
            raise ValueError("Immutable server capabilities required")
        if not isinstance(self.tool_sessions, MappingProxyType):
            raise ValueError("Server-owned immutable session directory required")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: type[ToolModel]
    result: type[ToolModel]
    capability: str
    authorize: Callable[[ToolContext, ToolModel], bool]
    handler: Callable[[ToolContext, ToolModel], Awaitable[ToolModel]]
    session_factory: Callable[[ToolContext], object] | None = None

    def __post_init__(self):
        for model in (self.parameters, self.result):
            if (
                not isinstance(model, type)
                or not issubclass(model, ToolModel)
                or any(
                    model.model_config.get(key) != value
                    for key, value in ToolModel.model_config.items()
                )
            ):
                raise ValueError("Strict tool models required")
        if not valid_text(self.capability, 128, nonblank=True) or not all(
            callable(value) for value in (self.authorize, self.handler)
        ):
            raise ValueError("Explicit authorization and handler required")
        if self.session_factory is not None and not callable(self.session_factory):
            raise ValueError("Invalid tool session factory")
        self.definition()

    def definition(self):
        return ToolDefinition(
            self.name,
            self.description,
            json.dumps(self.parameters.model_json_schema(), ensure_ascii=False, allow_nan=False),
        )


@dataclass(frozen=True)
class PreparedCall:
    call: ToolCall
    spec: ToolSpec
    arguments: ToolModel


@dataclass(frozen=True)
class GuardrailDecision:
    outcome: Literal["ALLOW", "DENY"]
    policy_id: str = "tool-capability-and-resource-v1"


class Guardrail:
    @staticmethod
    def decide(context: ToolContext, spec: ToolSpec, arguments: ToolModel) -> GuardrailDecision:
        if spec.capability not in context.capabilities:
            return GuardrailDecision("DENY")
        try:
            allowed = spec.authorize(context, arguments)
        except Exception:
            raise ToolFailure("TOOL_EXECUTION_FAILED") from None
        return GuardrailDecision("ALLOW" if allowed is True else "DENY")

    @classmethod
    def check(cls, context: ToolContext, spec: ToolSpec, arguments: ToolModel) -> GuardrailDecision:
        decision = cls.decide(context, spec, arguments)
        if decision.outcome == "DENY":
            raise ToolFailure("TOOL_PERMISSION_DENIED")
        return decision


@dataclass(frozen=True)
class ToolRegistry:
    specs: tuple[ToolSpec, ...] = ()

    def __post_init__(self):
        if (
            not isinstance(self.specs, tuple)
            or len(self.specs) > 16
            or any(not isinstance(spec, ToolSpec) for spec in self.specs)
            or len({spec.name for spec in self.specs}) != len(self.specs)
        ):
            raise ValueError("Unique immutable tool specifications required")

    def visible(self, context: ToolContext) -> tuple[ToolDefinition, ...]:
        return tuple(
            spec.definition() for spec in self.specs if spec.capability in context.capabilities
        )

    def bind_context(self, context: ToolContext) -> ToolContext:
        """每个Run创建私有工具会话；注册表/handler只共享无请求状态的依赖。"""
        if context.tool_sessions:
            raise ValueError("Tool context is already bound")
        sessions = {
            spec.name: spec.session_factory(context)
            for spec in self.specs
            if spec.session_factory is not None and spec.capability in context.capabilities
        }
        return replace(context, tool_sessions=MappingProxyType(sessions)) if sessions else context

    def prepare(
        self, calls: tuple[ToolCall, ...], context: ToolContext
    ) -> tuple[PreparedCall, ...]:
        prepared = []
        for call in calls:
            spec = next((spec for spec in self.specs if spec.name == call.name), None)
            if spec is None:
                raise ToolFailure("TOOL_NOT_AVAILABLE")
            try:
                arguments = spec.parameters.model_validate_json(call.arguments_json, strict=True)
            except ValidationError:
                raise ToolFailure("TOOL_INVALID_ARGUMENTS") from None
            Guardrail.check(context, spec, arguments)
            prepared.append(PreparedCall(call, spec, arguments))
        return tuple(prepared)

    async def invoke(self, prepared: PreparedCall, context: ToolContext) -> str:
        Guardrail.check(context, prepared.spec, prepared.arguments)
        try:
            result = await prepared.spec.handler(context, prepared.arguments)
        except ToolFailure:
            raise
        except Exception:
            raise ToolFailure("TOOL_EXECUTION_FAILED") from None
        try:
            result = prepared.spec.result.model_validate(result, strict=True)
            content = result.model_dump_json()
            if not valid_text(content, MAX_TOOL_RESULT_CHARS, nonblank=True):
                raise ValueError("Tool result budget exceeded")
        except (ValueError, TypeError):
            raise ToolFailure("TOOL_INVALID_RESULT") from None
        return content
