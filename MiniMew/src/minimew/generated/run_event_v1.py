# Generated from contracts/run_event_v1.json. Do not edit.
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]

MessageStatus = Literal["pending", "completed", "failed", "cancelled"]

ErrorCode = Literal[
    "PROVIDER_ERROR",
    "PROVIDER_TIMEOUT",
    "PROVIDER_INVALID_RESPONSE",
    "EXECUTION_EXPIRED",
    "EXECUTOR_INTERRUPTED",
    "MODEL_SNAPSHOT_MISSING",
    "MODEL_SNAPSHOT_INVALID",
    "CONTEXT_INVALID",
    "EXECUTION_STORAGE_UNAVAILABLE",
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
]


class RunCreatedData(ContractModel):
    status: Literal["pending"]
    assistant_message_id: UUID


class RunStartedData(ContractModel):
    status: Literal["running"]


class MessageDeltaData(ContractModel):
    message_id: UUID
    content: str = Field(min_length=1, max_length=4096)


class MessageCompletedData(ContractModel):
    message_id: UUID
    status: Literal["completed", "failed", "cancelled"]
    content: str = Field(max_length=100000)


class RunCompletedData(ContractModel):
    status: Literal["succeeded"]


class RunFailedData(ContractModel):
    status: Literal["failed"]
    error_code: ErrorCode


class RunCancelRequestedData(ContractModel):
    cancel_requested_at: AwareDatetime


class RunCancelledData(ContractModel):
    status: Literal["cancelled"]


class ToolStartedData(ContractModel):
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]{1,64}$")


class ToolCompletedData(ContractModel):
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]{1,64}$")


class ToolFailedData(ContractModel):
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]{1,64}$")
    error_code: ErrorCode


class RunEventBase(ContractModel):
    schema_version: Literal[1]
    event_id: UUID
    sequence: int = Field(ge=1)
    run_id: UUID
    thread_id: UUID
    timestamp: AwareDatetime

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_constant(cls, value):
        if type(value) is not int:
            raise ValueError("Expected an integer constant")
        return value


class RunCreatedEvent(RunEventBase):
    type: Literal["run.created"]
    data: RunCreatedData


class RunStartedEvent(RunEventBase):
    type: Literal["run.started"]
    data: RunStartedData


class MessageDeltaEvent(RunEventBase):
    type: Literal["message.delta"]
    data: MessageDeltaData


class MessageCompletedEvent(RunEventBase):
    type: Literal["message.completed"]
    data: MessageCompletedData


class RunCompletedEvent(RunEventBase):
    type: Literal["run.completed"]
    data: RunCompletedData


class RunFailedEvent(RunEventBase):
    type: Literal["run.failed"]
    data: RunFailedData


class RunCancel_RequestedEvent(RunEventBase):
    type: Literal["run.cancel_requested"]
    data: RunCancelRequestedData


class RunCancelledEvent(RunEventBase):
    type: Literal["run.cancelled"]
    data: RunCancelledData


class ToolStartedEvent(RunEventBase):
    type: Literal["tool.started"]
    data: ToolStartedData


class ToolCompletedEvent(RunEventBase):
    type: Literal["tool.completed"]
    data: ToolCompletedData


class ToolFailedEvent(RunEventBase):
    type: Literal["tool.failed"]
    data: ToolFailedData


RunEvent = Annotated[
    RunCreatedEvent
    | RunStartedEvent
    | MessageDeltaEvent
    | MessageCompletedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancel_RequestedEvent
    | RunCancelledEvent
    | ToolStartedEvent
    | ToolCompletedEvent
    | ToolFailedEvent,
    Field(discriminator="type"),
]
EVENT_ADAPTER = TypeAdapter(RunEvent)
