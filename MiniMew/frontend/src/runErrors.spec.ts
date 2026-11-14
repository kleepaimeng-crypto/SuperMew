import { expect, it } from "vitest";
import source from "../../contracts/run_event_v1.json?raw";
import { ids, run, started, event, time } from "../tests/fixtures/chat";
import { createRunProjection, reduceRunEvent } from "./events/runEventReducer";
import { errorPresentation, failureForRun } from "./runErrors";
import type { ErrorCode } from "./generated/run-event-v1";

const codes = JSON.parse(source).$defs.ErrorCode.enum as ErrorCode[];
it.each(codes)("错误%s有受控中文说明和正确操作边界", (code) => {
  const presentation = errorPresentation(code);
  expect(presentation.code).toBe(code);
  expect(presentation.reason).toMatch(/[\u4e00-\u9fff]/);
  expect(presentation.action).not.toBe("");
});
it("未知错误只显示受控类型，不回显原始服务消息", () => {
  const result = errorPresentation("sk-secret-and-document-content");
  expect(result.code).toBe("UNKNOWN_ERROR");
  expect(JSON.stringify(result)).not.toContain("sk-secret");
});
it("实时失败与刷新持久历史给出相同解释，成功和取消不变成失败", () => {
  let projection = createRunProjection({
    threadId: ids.thread,
    runId: ids.run,
    assistantMessageId: ids.assistant,
  });
  expect(failureForRun(run, projection)).toBeNull();
  for (const value of [
    ...started,
    event("message.completed", 3, {
      message_id: ids.assistant,
      status: "failed",
      content: "",
    }),
    event("run.failed", 4, {
      status: "failed",
      error_code: "KNOWLEDGE_RESULT_INVALID",
    }),
  ]) {
    projection = reduceRunEvent(projection, value).state;
  }
  const live = failureForRun(run, projection);
  const history = failureForRun(
    {
      ...run,
      status: "failed",
      error_code: "KNOWLEDGE_RESULT_INVALID",
      finished_at: time,
    },
    createRunProjection(projection),
  );
  expect(live).toEqual(history);
  expect(live?.reason).toContain("检索资料");
  expect(failureForRun({ ...run, status: "succeeded" }, projection)).toBeNull();
  expect(failureForRun({ ...run, status: "cancelled" }, projection)).toBeNull();
});
