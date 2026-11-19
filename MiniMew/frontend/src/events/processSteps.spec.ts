import { it, expect } from "vitest";
import { event, started, ids } from "../../tests/fixtures/chat";
import { createRunProjection, reduceRunEvent } from "./runEventReducer";
import { parseRunEvent } from "./runEventContract";
import type {
  ProcessStepData,
  ProcessDetailData,
} from "../generated/run-event-v1";
const data: ProcessStepData = {
  step_id: "a".repeat(32),
  parent_step_id: "",
  tool_call_id: "",
  kind: "model",
  status: "running",
  query: "",
  summary: "",
  count: 0,
  sources: [],
  outcome: "",
  elapsed_ms: 0,
};
function running() {
  let s = createRunProjection({
    threadId: ids.thread,
    runId: ids.run,
    assistantMessageId: ids.assistant,
  });
  for (const e of started) s = reduceRunEvent(s, e).state;
  return s;
}
it("steps replay, deduplicate and isolate Run/parent identities", () => {
  const first = event("process.step", 3, data);
  let s = reduceRunEvent(running(), parseRunEvent(first)).state;
  expect(s.steps).toHaveLength(1);
  expect(reduceRunEvent(s, first).kind).toBe("duplicate");
  expect(
    reduceRunEvent(s, { ...first, run_id: ids.user, sequence: 4 }).kind,
  ).toBe("foreign");
  const orphan = event("process.step", 4, {
    ...data,
    step_id: "b".repeat(32),
    parent_step_id: "c".repeat(32),
  });
  expect(reduceRunEvent(s, orphan).kind).toBe("invalid");
  const done = event("process.step", 4, {
    ...data,
    status: "completed",
    elapsed_ms: 30,
  });
  s = reduceRunEvent(s, parseRunEvent(done)).state;
  expect(s.steps[0]?.status).toBe("completed");
  expect(s.cursor).toBe(4);
  expect(running().steps).toEqual([]);
});
it("bounded arrays, numeric maxima and extra private fields are rejected", () => {
  const e = event("process.step", 3, data);
  for (const patch of [
    { count: 1000001 },
    { sources: Array(7).fill({}) },
    { reasoning: "private" },
  ])
    expect(() =>
      parseRunEvent({ ...e, data: { ...data, ...patch } }),
    ).toThrow();
});

const detail: ProcessDetailData = {
  step_id: data.step_id,
  stage: "start",
  phase: "initial",
  purpose: "",
  attempt: 0,
  missing: [],
  questions: [],
  sources: [],
};
it("detail replays on its existing step, preserves cursor on invalid/foreign events", () => {
  let s = reduceRunEvent(running(), event("process.step", 3, data)).state;
  const first = event("process.detail", 4, detail);
  s = reduceRunEvent(s, parseRunEvent(first)).state;
  expect(s.stepDetails).toEqual([detail]);
  expect(reduceRunEvent(s, first).kind).toBe("duplicate");
  expect(
    reduceRunEvent(s, { ...first, run_id: ids.user, sequence: 5 }).kind,
  ).toBe("foreign");
  const result = event("process.detail", 5, {
    ...detail,
    stage: "result",
    missing: ["校验命令"],
  });
  s = reduceRunEvent(s, parseRunEvent(result)).state;
  expect(s.stepDetails[0]?.missing).toEqual(["校验命令"]);
  expect(reduceRunEvent(s, { ...result, sequence: 6 }).kind).toBe("invalid");
  expect(s.cursor).toBe(5);
  expect(reduceRunEvent(running(), first).kind).not.toBe("applied");
});
it("metadata does not fabricate missing old fields and checks nested scalar bounds", () => {
  expect(running().stepDetails).toEqual([]);
  for (const patch of [
    { missing: ["x".repeat(257)] },
    { questions: [""] },
    { reasoning: "private" },
  ])
    expect(() =>
      parseRunEvent(event("process.detail", 3, { ...detail, ...patch })),
    ).toThrow();
});
