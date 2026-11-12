import { expect, it } from "vitest";
import type { ErrorCode } from "../generated/run-event-v1";
import { parseRun } from "../chat";
import { event, ids, run, started, time } from "../../tests/fixtures/chat";
import { parseRunEvent } from "./runEventContract";
import { createRunProjection, reduceRunEvent } from "./runEventReducer";

const codes: ErrorCode[] = [
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
];

it.each(codes)("接受正式工具错误 %s，并在消息完成后投影失败终态", (code) => {
  expect(
    parseRun(
      { ...run, status: "failed", finished_at: time, error_code: code },
      ids.thread,
    ).error_code,
  ).toBe(code);
  let state = createRunProjection({
    threadId: ids.thread,
    runId: ids.run,
    assistantMessageId: ids.assistant,
  });
  const terminal = event("run.failed", 4, {
    status: "failed",
    error_code: code,
  });
  for (const value of [
    ...started,
    event("message.completed", 3, {
      message_id: ids.assistant,
      status: "failed",
      content: "",
    }),
    terminal,
  ]) {
    const result = reduceRunEvent(state, parseRunEvent(value));
    expect(result.kind).toBe("applied");
    state = result.state;
  }
  expect(state.runStatus).toBe("failed");
  expect(state.messageStatus).toBe("failed");
  expect(state.errorCode).toBe(code);
  expect(state.content).toBe("");
  expect(reduceRunEvent(state, terminal).kind).toBe("duplicate");
});
