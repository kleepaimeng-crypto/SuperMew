"""模型调用边界：Provider 不拥有业务 Run 或数据库事务。"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from minimew.execution.diagnostics import Diagnostic
from minimew.execution.messages import (
    InputMessage,
    ModelTurn,
    ToolDefinition,
    validate_messages,
)
from minimew.model_control import ModelSnapshot


@dataclass(frozen=True)
class ProviderRequest:
    run_id: UUID
    thread_id: UUID
    snapshot: ModelSnapshot
    messages: tuple[InputMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    instructions: str = ""
    purpose: Literal["answer", "grade", "plan", "rewrite", "repair"] = "answer"
    private_output: bool = False
    force_answer: bool = False

    def __post_init__(self):
        if (
            type(self.private_output) is not bool
            or type(self.force_answer) is not bool
            or (self.purpose == "repair") != self.private_output
        ):
            raise ValueError("Repair requests require private output")
        if self.purpose == "repair" and self.tools:
            raise ValueError("Repair requests cannot expose tools")
        validate_messages(self.messages, self.tools, self.instructions)


MAX_DELTA_CHARS = 4096


@dataclass(frozen=True)
class ProviderDelta:
    """一批可公开的答案正文；Adapter 可以先合并上游的小分片。"""

    content: str


@dataclass(frozen=True)
class ProviderCompleted:
    """Adapter已确认结束的模型回合，Run终态仍由执行器的持久事务拥有。"""

    turn: ModelTurn


ProviderEvent = ProviderDelta | ProviderCompleted


class Provider(Protocol):
    def stream(self, request: ProviderRequest) -> AsyncGenerator[ProviderEvent, None]: ...


class ProviderFailure(Exception):
    def __init__(
        self,
        code: Literal[
            "PROVIDER_ERROR", "PROVIDER_TIMEOUT", "PROVIDER_INVALID_RESPONSE"
        ] = "PROVIDER_ERROR",
        *,
        retryable: bool = False,
        retry_after: float = 0,
        diagnostic: Diagnostic | None = None,
    ):
        if code not in {"PROVIDER_ERROR", "PROVIDER_TIMEOUT", "PROVIDER_INVALID_RESPONSE"}:
            raise ValueError("Unsupported Provider error code")
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.diagnostic = diagnostic
        super().__init__(code)
