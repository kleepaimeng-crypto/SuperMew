from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WaitingPrompt(BaseModel):
    checkpoint_id: UUID
    prompt: str
    expires_at: datetime


class ResumeCredential(WaitingPrompt):
    token: str


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    checkpoint_id: UUID
    token: str = Field(min_length=32, max_length=128, repr=False)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    answer: str = Field(min_length=1, max_length=1024)

    @field_validator("answer")
    @classmethod
    def valid_answer(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Nonblank answer without NUL required")
        return value


class ResumeConflict(Exception):
    pass
