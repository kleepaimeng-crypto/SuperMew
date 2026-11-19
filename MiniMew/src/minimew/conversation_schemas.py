from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from minimew.generated.run_event_v1 import ErrorCode, MessageStatus, RunEvent, RunStatus
from minimew.skills.schemas import SkillSelection, SkillSummary


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    content: str = Field(min_length=1, max_length=20000)
    skill: SkillSelection | None = None

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Content must be nonblank and contain no NUL")
        return value  # 保存原正文，幂等比较也使用原文，不偷偷规范化消息。


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    thread_id: UUID
    run_id: UUID
    sequence: int
    role: Literal["user", "assistant"]
    status: MessageStatus
    content: str
    created_at: datetime


class RunResponse(BaseModel):
    id: UUID
    thread_id: UUID
    status: RunStatus
    user_message_id: UUID
    assistant_message_id: UUID
    created_at: datetime
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error_code: ErrorCode | None = None
    skill: SkillSummary | None = None


class MessagePage(BaseModel):
    items: list[MessageResponse]
    previous_cursor: int | None


class RunEventPage(BaseModel):
    items: list[RunEvent]
    next_cursor: int = Field(ge=0)
    has_more: bool
