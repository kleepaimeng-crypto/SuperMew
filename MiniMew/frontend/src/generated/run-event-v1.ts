// Generated from contracts/run_event_v1.json. Do not edit.
export type RunStatus =
  "pending" | "running" | "succeeded" | "failed" | "cancelled";
export type MessageStatus = "pending" | "completed" | "failed" | "cancelled";
export type ErrorCode =
  | "PROVIDER_ERROR"
  | "PROVIDER_TIMEOUT"
  | "PROVIDER_INVALID_RESPONSE"
  | "EXECUTION_EXPIRED"
  | "EXECUTOR_INTERRUPTED"
  | "MODEL_SNAPSHOT_MISSING"
  | "MODEL_SNAPSHOT_INVALID"
  | "CONTEXT_INVALID"
  | "EXECUTION_STORAGE_UNAVAILABLE";
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
export type RunEvent =
  | RunCreatedEvent
  | RunStartedEvent
  | MessageDeltaEvent
  | MessageCompletedEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunCancel_RequestedEvent
  | RunCancelledEvent;
