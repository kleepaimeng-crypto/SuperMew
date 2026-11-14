// Generated from contracts/run_event_v1.json. Do not edit.
export type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "waiting_input"
  | "resume_pending";
export type MessageStatus =
  "pending" | "completed" | "failed" | "cancelled" | "waiting_input";
export type ErrorCode =
  | "PROVIDER_ERROR"
  | "PROVIDER_TIMEOUT"
  | "PROVIDER_INVALID_RESPONSE"
  | "EXECUTION_EXPIRED"
  | "EXECUTOR_INTERRUPTED"
  | "MODEL_SNAPSHOT_MISSING"
  | "MODEL_SNAPSHOT_INVALID"
  | "CONTEXT_INVALID"
  | "EXECUTION_STORAGE_UNAVAILABLE"
  | "TOOL_NOT_AVAILABLE"
  | "TOOL_INVALID_ARGUMENTS"
  | "TOOL_PERMISSION_DENIED"
  | "TOOL_EXECUTION_FAILED"
  | "TOOL_INVALID_RESULT"
  | "AGENT_BUDGET_EXCEEDED"
  | "AGENT_DEADLINE_EXCEEDED"
  | "KNOWLEDGE_ENCODING_FAILED"
  | "KNOWLEDGE_RETRIEVAL_FAILED"
  | "KNOWLEDGE_SCOPE_INVALID"
  | "KNOWLEDGE_RESULT_INVALID"
  | "KNOWLEDGE_QUERY_TOO_LONG"
  | "HITL_EXPIRED"
  | "HITL_CHECKPOINT_INVALID";
export interface RunCreatedData {
  status: "pending";
  assistant_message_id: string;
}
export interface RunStartedData {
  status: "running";
}
export interface MessageDeltaData {
  message_id: string;
  content: string;
}
export interface MessageCompletedData {
  message_id: string;
  status: "completed" | "failed" | "cancelled";
  content: string;
}
export interface RunCompletedData {
  status: "succeeded";
}
export interface RunFailedData {
  status: "failed";
  error_code: ErrorCode;
}
export interface RunCancelRequestedData {
  cancel_requested_at: string;
}
export interface RunCancelledData {
  status: "cancelled";
}
export interface ToolStartedData {
  tool_call_id: string;
  tool_name: string;
}
export interface ToolCompletedData {
  tool_call_id: string;
  tool_name: string;
}
export interface ToolFailedData {
  tool_call_id: string;
  tool_name: string;
  error_code: ErrorCode;
}
export interface HitlRequiredData {
  checkpoint_id: string;
  prompt: string;
  expires_at: string;
}
export interface HitlAcceptedData {
  checkpoint_id: string;
}
export interface RunEventBase {
  schema_version: 1;
  event_id: string;
  sequence: number;
  run_id: string;
  thread_id: string;
  timestamp: string;
}
export interface RunCreatedEvent extends RunEventBase {
  type: "run.created";
  data: RunCreatedData;
}
export interface RunStartedEvent extends RunEventBase {
  type: "run.started";
  data: RunStartedData;
}
export interface MessageDeltaEvent extends RunEventBase {
  type: "message.delta";
  data: MessageDeltaData;
}
export interface MessageCompletedEvent extends RunEventBase {
  type: "message.completed";
  data: MessageCompletedData;
}
export interface RunCompletedEvent extends RunEventBase {
  type: "run.completed";
  data: RunCompletedData;
}
export interface RunFailedEvent extends RunEventBase {
  type: "run.failed";
  data: RunFailedData;
}
export interface RunCancel_RequestedEvent extends RunEventBase {
  type: "run.cancel_requested";
  data: RunCancelRequestedData;
}
export interface RunCancelledEvent extends RunEventBase {
  type: "run.cancelled";
  data: RunCancelledData;
}
export interface ToolStartedEvent extends RunEventBase {
  type: "tool.started";
  data: ToolStartedData;
}
export interface ToolCompletedEvent extends RunEventBase {
  type: "tool.completed";
  data: ToolCompletedData;
}
export interface ToolFailedEvent extends RunEventBase {
  type: "tool.failed";
  data: ToolFailedData;
}
export interface HitlRequiredEvent extends RunEventBase {
  type: "hitl.required";
  data: HitlRequiredData;
}
export interface HitlAcceptedEvent extends RunEventBase {
  type: "hitl.accepted";
  data: HitlAcceptedData;
}
export interface RunResumedEvent extends RunEventBase {
  type: "run.resumed";
  data: RunStartedData;
}
export type RunEvent =
  | RunCreatedEvent
  | RunStartedEvent
  | MessageDeltaEvent
  | MessageCompletedEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunCancel_RequestedEvent
  | RunCancelledEvent
  | ToolStartedEvent
  | ToolCompletedEvent
  | ToolFailedEvent
  | HitlRequiredEvent
  | HitlAcceptedEvent
  | RunResumedEvent;
