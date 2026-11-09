import type { RunEvent } from "../../src/generated/run-event-v1";
import type { ChatMessage, ChatRun } from "../../src/chat";

export const ids = {
  thread: "00000000-0000-0000-0000-000000000001",
  run: "00000000-0000-0000-0000-000000000002",
  user: "00000000-0000-0000-0000-000000000003",
  assistant: "00000000-0000-0000-0000-000000000004",
};
export const time = "2026-11-09T00:00:00Z";
export const run: ChatRun = {
  id: ids.run,
  thread_id: ids.thread,
  user_message_id: ids.user,
  assistant_message_id: ids.assistant,
  status: "running",
  created_at: time,
  finished_at: null,
  cancel_requested_at: null,
  error_code: null,
};
export const messages: ChatMessage[] = [
  {
    id: ids.user,
    run_id: ids.run,
    thread_id: ids.thread,
    role: "user",
    status: "completed",
    content: "你好",
    sequence: 1,
    created_at: time,
  },
  {
    id: ids.assistant,
    run_id: ids.run,
    thread_id: ids.thread,
    role: "assistant",
    status: "pending",
    content: "",
    sequence: 2,
    created_at: time,
  },
];
export function event<T extends RunEvent["type"]>(
  type: T,
  sequence: number,
  data: Extract<RunEvent, { type: T }>["data"],
): Extract<RunEvent, { type: T }> {
  return {
    schema_version: 1,
    event_id: `10000000-0000-0000-0000-${String(sequence).padStart(12, "0")}`,
    thread_id: ids.thread,
    run_id: ids.run,
    sequence,
    timestamp: time,
    type,
    data,
  } as Extract<RunEvent, { type: T }>;
}
export const started = [
  event("run.created", 1, {
    status: "pending",
    assistant_message_id: ids.assistant,
  }),
  event("run.started", 2, { status: "running" }),
];
export const body = event("message.delta", 3, {
  message_id: ids.assistant,
  content: "今天",
});
export const success = [
  ...started,
  body,
  event("message.completed", 4, {
    message_id: ids.assistant,
    status: "completed",
    content: "今天天气很好",
  }),
  event("run.completed", 5, { status: "succeeded" }),
];
export const cancelled = [
  event("run.cancel_requested", 4, { cancel_requested_at: time }),
  event("message.completed", 5, {
    message_id: ids.assistant,
    status: "cancelled",
    content: "今天",
  }),
  event("run.cancelled", 6, { status: "cancelled" }),
];
export const frame = (input: RunEvent) =>
  `event: ${input.type}\ndata: ${JSON.stringify(input)}\n\n`;
export function eventResponse(
  inputs: RunEvent[],
  signal?: AbortSignal,
  open = false,
) {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        inputs.forEach((input) =>
          controller.enqueue(new TextEncoder().encode(frame(input))),
        );
        if (open)
          signal?.addEventListener(
            "abort",
            () => controller.error(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        else controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}
