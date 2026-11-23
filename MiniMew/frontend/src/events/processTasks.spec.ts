import { it, expect } from "vitest";
import type {
  ProcessStepData,
  ProcessDetailData,
} from "../generated/run-event-v1";
import {
  processTasks,
  taskTitle,
  taskState,
  taskElapsed,
  decisionSummary,
  elapsed,
  materialCount,
} from "./processTasks";
function step(
  id: string,
  kind: ProcessStepData["kind"],
  parent = "",
  call = "search",
): ProcessStepData {
  return {
    step_id: id.repeat(32),
    parent_step_id: parent.repeat(32),
    tool_call_id: call,
    kind,
    status: "completed",
    query: "",
    summary: "",
    sources: [],
    count: 6,
    outcome: "",
    elapsed_ms: 30,
  };
}
function detail(
  s: ProcessStepData,
  patch: Partial<ProcessDetailData>,
): ProcessDetailData {
  return {
    step_id: s.step_id,
    stage: "result",
    phase: "",
    purpose: "",
    attempt: 0,
    missing: [],
    questions: [],
    sources: [],
    ...patch,
  };
}
it("parallel algorithm records stay with their own child and Tool Call", () => {
  const plan = step("a", "plan"),
    one = { ...step("b", "subquestion"), query: "备份" },
    two = { ...step("c", "subquestion"), query: "依赖" },
    other = step("d", "subquestion", "", "other");
  const tasks = processTasks(
    [plan, two, one, step("e", "dense", "b"), step("f", "dense", "c"), other],
    [detail(plan, { questions: ["备份", "依赖"] })],
  );
  expect(tasks).toHaveLength(2);
  expect(tasks[0]?.children.map((c) => c.number)).toEqual([1, 2]);
  expect(tasks[0]?.children[0]?.records.map((s) => s.step_id)).toEqual([
    "e".repeat(32),
  ]);
  expect(tasks[1]?.step.tool_call_id).toBe("other");
});
it("truthful phase/purpose labels, pending children and legacy algorithm records", () => {
  const retrieval = step("a", "retrieval"),
    model = step("b", "model"),
    plan = step("c", "plan");
  const tasks = processTasks(
    [retrieval, model, plan, step("d", "rerank")],
    [
      detail(retrieval, { phase: "rewritten" }),
      detail(model, { purpose: "answer_repair", attempt: 2 }),
      detail(plan, { questions: ["备份", "依赖"] }),
    ],
  );
  expect(taskTitle(tasks[0]!)).toBe("调整问题后补查");
  expect(taskTitle(tasks[1]!)).toBe("校正回答格式（第2次尝试）");
  expect(taskState(tasks[2]!, "running")).toBe("正在安排补查");
  expect(taskState(tasks[2]!, "failed")).toBe("未全部执行");
  expect(taskTitle(tasks[3]!)).toBe("重排候选");
  expect(materialCount(model)).toBe("");
  expect(elapsed(12)).toBe("<0.1秒");
  expect(taskTitle(processTasks([model], [])[0]!)).toBe("模型请求");
});
it("decisionSummary shows structured summary and falls back cleanly for legacy runs", () => {
  const modern = {
    ...step("a", "model", "", ""),
    summary: "决定调用：知识库检索（“启动前准备”）",
    outcome: "tool_calls",
    elapsed_ms: 1400,
  };
  const legacy = {
    ...step("b", "model", "", ""),
    summary: "模型请求结束",
    outcome: "tool_calls",
    elapsed_ms: 0,
  };
  const rewrite = {
    ...step("c", "rewrite", "", "search"),
    summary: "改写查询：网络备份与校验命令",
  };
  const tasks = processTasks([modern, legacy, rewrite], []);
  expect(decisionSummary(tasks[0]!)).toBe(
    "决定调用：知识库检索（“启动前准备”）",
  );
  expect(taskElapsed(tasks[0]!)).toBe("1.4秒");
  expect(decisionSummary(tasks[1]!)).toBe("决定调用工具获取补充资料");
  expect(taskElapsed(tasks[1]!)).toBe("");
  expect(decisionSummary(tasks[2]!)).toBe("改写查询：网络备份与校验命令");
});
