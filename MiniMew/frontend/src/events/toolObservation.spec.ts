import { expect, it } from "vitest";
import { started, ids, time } from "../../tests/fixtures/chat";
import { parseRunEvent } from "./runEventContract";
import {
  createRunProjection,
  reduceRunEvent,
  type RunProjection,
} from "./runEventReducer";
import type { RunEvent } from "../generated/run-event-v1";

const tool = { tool_call_id: "c1", tool_name: "knowledge_search" };
function event(type: string, sequence: number, data: unknown): RunEvent {
  return parseRunEvent({
    ...started[1],
    event_id: crypto.randomUUID(),
    type,
    sequence,
    data,
  });
}
function apply(state: RunProjection, input: RunEvent) {
  const result = reduceRunEvent(state, input);
  expect(result.kind).toBe("applied");
  return result.state;
}
function running() {
  return started.reduce(
    apply,
    createRunProjection({
      threadId: ids.thread,
      runId: ids.run,
      assistantMessageId: ids.assistant,
    }),
  );
}

it("工具开始/完成可重放且去重，完成工具不会提前结束Run", () => {
  const begin = event("tool.started", 3, tool);
  const state = apply(running(), begin);
  expect(state.tools[0]?.status).toBe("running");
  expect(reduceRunEvent(state, begin).kind).toBe("duplicate");
  const finished = apply(state, event("tool.completed", 4, tool));
  expect(finished.tools[0]?.status).toBe("completed");
  expect(finished.runStatus).toBe("running");
  expect(reduceRunEvent(running(), event("tool.completed", 3, tool)).kind).toBe(
    "invalid",
  );
  expect(reduceRunEvent(state, event("tool.completed", 5, tool)).kind).toBe(
    "gap",
  );
});

it("Run取消终态收束尚未结束的工具，不伪造tool.completed", () => {
  let state = apply(running(), event("tool.started", 3, tool));
  state = apply(
    state,
    event("run.cancel_requested", 4, { cancel_requested_at: time }),
  );
  state = apply(
    state,
    event("message.completed", 5, {
      message_id: ids.assistant,
      status: "cancelled",
      content: "",
    }),
  );
  state = apply(state, event("run.cancelled", 6, { status: "cancelled" }));
  expect(state.tools[0]?.status).toBe("cancelled");
});

it("公开工具失败保留稳定code，不展示参数或原始结果", () => {
  const state = apply(running(), event("tool.started", 3, tool));
  const failure = apply(
    state,
    event("tool.failed", 4, {
      ...tool,
      error_code: "KNOWLEDGE_RETRIEVAL_FAILED",
    }),
  );
  expect(failure.tools[0]?.errorCode).toBe("KNOWLEDGE_RETRIEVAL_FAILED");
  expect(() =>
    event("tool.started", 3, { ...tool, arguments: "private" }),
  ).toThrow();
  expect(() =>
    event("tool.started", 3, { ...tool, tool_name: "<script>" }),
  ).toThrow();
});

it("正文中的检索声明和引用不会生成工具执行记录", () => {
  let state = apply(
    running(),
    event("message.delta", 3, {
      message_id: ids.assistant,
      content: "本次已经检索知识库，依据[E1]。",
    }),
  );
  expect(state.tools).toEqual([]);
  state = apply(
    state,
    event("message.completed", 4, {
      message_id: ids.assistant,
      status: "failed",
      content: "本次已经检索知识库，依据[E1]。",
    }),
  );
  state = apply(
    state,
    event("run.failed", 5, {
      status: "failed",
      error_code: "PROVIDER_INVALID_RESPONSE",
    }),
  );
  expect(state.tools).toEqual([]);
  expect(state.runStatus).toBe("failed");
});
