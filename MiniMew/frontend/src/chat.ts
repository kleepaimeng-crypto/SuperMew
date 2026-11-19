import type { components } from "./generated/api";
import { parseSkill } from "./skills";
import {
  isDateTime,
  isEventValue,
  isRecord,
  isUuid,
} from "./events/runEventContract";

export type ChatMessage = components["schemas"]["MessageResponse"];
export type ChatRun = components["schemas"]["RunResponse"];
export type MessagePage = components["schemas"]["MessagePage"];
export class ChatProtocolError extends Error {
  constructor() {
    super("Invalid chat response");
  }
}
export function terminal(status: string): boolean {
  return ["succeeded", "failed", "cancelled"].includes(status);
}
export function parseRun(
  value: unknown,
  threadId: string,
  runId?: string,
): ChatRun {
  if (
    !isRecord(value) ||
    !isUuid(value.id) ||
    value.thread_id !== threadId ||
    (runId !== undefined && value.id !== runId) ||
    !isUuid(value.user_message_id) ||
    !isUuid(value.assistant_message_id) ||
    value.user_message_id === value.assistant_message_id ||
    !isEventValue(value.status, "RunStatus") ||
    !isDateTime(value.created_at) ||
    (value.finished_at != null && !isDateTime(value.finished_at)) ||
    (value.cancel_requested_at != null &&
      !isDateTime(value.cancel_requested_at)) ||
    (value.error_code != null &&
      !isEventValue(value.error_code, "ErrorCode")) ||
    (terminal(String(value.status)) && !isDateTime(value.finished_at)) ||
    (value.status === "failed" &&
      !isEventValue(value.error_code, "ErrorCode")) ||
    (value.status === "cancelled" && !isDateTime(value.cancel_requested_at))
  )
    throw new ChatProtocolError();
  return {
    id: value.id,
    thread_id: threadId,
    status: value.status,
    user_message_id: value.user_message_id,
    assistant_message_id: value.assistant_message_id,
    created_at: value.created_at,
    finished_at: value.finished_at ?? null,
    cancel_requested_at: value.cancel_requested_at ?? null,
    error_code: value.error_code ?? null,
    ...(value.skill !== undefined
      ? { skill: value.skill === null ? null : parseSkill(value.skill) }
      : {}),
  } as ChatRun;
}
export function parseMessagePage(
  value: unknown,
  threadId: string,
  before?: number,
): MessagePage {
  if (
    !isRecord(value) ||
    !Array.isArray(value.items) ||
    value.items.length > 100 ||
    !(
      value.previous_cursor === null ||
      (Number.isSafeInteger(value.previous_cursor) &&
        Number(value.previous_cursor) > 0)
    )
  )
    throw new ChatProtocolError();
  const ids = new Set<string>();
  let previous = 0;
  const items = value.items.map((item: unknown) => {
    if (
      !isRecord(item) ||
      !isUuid(item.id) ||
      !isUuid(item.run_id) ||
      item.thread_id !== threadId ||
      !["user", "assistant"].includes(String(item.role)) ||
      !isEventValue(item.status, "MessageStatus") ||
      (item.role === "user" && item.status !== "completed") ||
      typeof item.content !== "string" ||
      [...item.content].length > 100000 ||
      !isDateTime(item.created_at) ||
      !Number.isSafeInteger(item.sequence) ||
      Number(item.sequence) <= previous ||
      (before !== undefined && Number(item.sequence) >= before) ||
      ids.has(item.id)
    )
      throw new ChatProtocolError();
    ids.add(item.id);
    previous = Number(item.sequence);
    return {
      id: item.id,
      thread_id: threadId,
      run_id: item.run_id,
      sequence: item.sequence,
      role: item.role,
      status: item.status,
      content: item.content,
      created_at: item.created_at,
    } as ChatMessage;
  });
  if (
    value.previous_cursor !== null &&
    (!items.length || value.previous_cursor !== items[0]!.sequence)
  )
    throw new ChatProtocolError();
  return { items, previous_cursor: value.previous_cursor as number | null };
}
