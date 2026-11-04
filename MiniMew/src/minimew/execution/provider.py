"""模型调用边界：Provider 不拥有业务 Run 或数据库事务。"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from minimew.model_control import ModelSnapshot


@dataclass(frozen=True)
class InputMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ProviderRequest:
    run_id: UUID
    thread_id: UUID
    snapshot: ModelSnapshot
    messages: tuple[InputMessage, ...]


MAX_DELTA_CHARS = 4096
MAX_OUTPUT_CHARS = 100000


@dataclass(frozen=True)
class ProviderDelta:
    """一批可公开的答案正文；Adapter 可以先合并上游的小分片。"""

    content: str


@dataclass(frozen=True)
class ProviderCompleted:
    """Adapter 确认正常完成后的完整答案；迭代器耗尽本身不表示成功。"""

    content: str


ProviderEvent = ProviderDelta | ProviderCompleted


class Provider(Protocol):
    def stream(self, request: ProviderRequest) -> AsyncGenerator[ProviderEvent, None]: ...


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


class ProviderFailure(Exception):
    def __init__(
        self,
        code: Literal[
            "PROVIDER_ERROR", "PROVIDER_TIMEOUT", "PROVIDER_INVALID_RESPONSE"
        ] = "PROVIDER_ERROR",
        *,
        retryable: bool = False,
        retry_after: float = 0,
    ):
        if code not in {"PROVIDER_ERROR", "PROVIDER_TIMEOUT", "PROVIDER_INVALID_RESPONSE"}:
            raise ValueError("Unsupported Provider error code")
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(code)
