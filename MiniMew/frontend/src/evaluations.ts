import type { components } from "./generated/api";
import { ApiError } from "./api";
import { isId } from "./documents";
export type CorpusPage = components["schemas"]["CorpusPage"];
export type JobPage = components["schemas"]["JobPage"];
export type Job = components["schemas"]["JobView"];
export type Detail = components["schemas"]["CaseDetail"];
export type Artifact = components["schemas"]["ArtifactV2"];
export const active = (s: string) => ["pending", "running"].includes(s);
export const label = (s: string) =>
  ({
    pending: "排队中",
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    generating: "生成中",
    waiting: "等待补充",
    accepted: "已接收补充",
    generated: "答卷已保存",
    judging: "评分中",
    pass: "通过",
    fail: "未通过",
    insufficient: "评价不足",
    incomparable: "不可比较",
  })[s] ?? s;
export const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null;
const invalid = (): never => {
  throw new Error("Invalid evaluation response");
};
const nullable = (v: unknown) => v === null || typeof v === "string";
export function page<T>(v: unknown, check: (x: unknown) => boolean): T {
  if (
    !record(v) ||
    !Array.isArray(v.items) ||
    !v.items.every(check) ||
    !(
      v.next_offset === null ||
      (Number.isSafeInteger(v.next_offset) && Number(v.next_offset) >= 0)
    )
  )
    return invalid();
  return v as T;
}
export const corpusPage = (v: unknown) =>
  page<CorpusPage>(
    v,
    (x) =>
      record(x) &&
      isId(x.corpus_id) &&
      Array.isArray(x.source_ids) &&
      x.source_ids.every((y) => typeof y === "string") &&
      typeof x.expires_at === "string" &&
      Number.isFinite(Date.parse(x.expires_at)),
  );
export const jobPage = (v: unknown) =>
  page<JobPage>(
    v,
    (x) =>
      record(x) &&
      isId(x.job_id) &&
      typeof x.dataset_id === "string" &&
      ["pending", "running", "completed", "failed", "cancelled"].includes(
        String(x.status),
      ) &&
      nullable(x.decision) &&
      typeof x.created_at === "string",
  );
export function job(v: unknown): Job {
  if (
    !record(v) ||
    !isId(v.job_id) ||
    !["pending", "running", "completed", "failed", "cancelled"].includes(
      String(v.status),
    ) ||
    typeof v.cancel_requested !== "boolean" ||
    !nullable(v.error_code) ||
    !Array.isArray(v.cases) ||
    !v.cases.every(
      (c) =>
        record(c) &&
        typeof c.case_id === "string" &&
        [
          "pending",
          "generating",
          "waiting",
          "accepted",
          "generated",
          "judging",
          "completed",
          "failed",
          "cancelled",
        ].includes(String(c.status)) &&
        nullable(c.error_code),
    )
  )
    return invalid();
  return v as Job;
}
export function detail(v: unknown): Detail {
  if (
    !record(v) ||
    typeof v.case_id !== "string" ||
    typeof v.question !== "string" ||
    typeof v.status !== "string" ||
    !nullable(v.answer) ||
    !nullable(v.error_code) ||
    !nullable(v.judge_error) ||
    !Array.isArray(v.materials) ||
    !v.materials.every(
      (m) =>
        record(m) &&
        typeof m.source_id === "string" &&
        typeof m.material_id === "string" &&
        Number.isSafeInteger(m.start) &&
        Number.isSafeInteger(m.end),
    ) ||
    !["requested", "accepted", "resumed"].every(
      (k) => typeof v[k] === "boolean",
    )
  )
    return invalid();
  if (
    v.counts !== null &&
    (!record(v.counts) ||
      !["model_attempts", "retrieval_calls", "tool_calls"].every((k) =>
        Number.isSafeInteger((v.counts as Record<string, unknown>)[k]),
      ))
  )
    return invalid();
  if (
    v.judge !== null &&
    (!record(v.judge) ||
      !["correctness", "completeness", "grounding"].every((k) =>
        [0, 1, 2].includes(Number((v.judge as Record<string, unknown>)[k])),
      ))
  )
    return invalid();
  return v as Detail;
}
export function artifact(v: unknown): Artifact {
  if (
    !record(v) ||
    v.schema_version !== 2 ||
    v.scorer_version !== "controlled-judge-v2" ||
    !["pass", "fail", "insufficient", "incomparable"].includes(
      String(v.decision),
    ) ||
    !Array.isArray(v.evaluations) ||
    !Array.isArray(v.judge_deltas) ||
    !record(v.structural) ||
    !record(v.structural.gate) ||
    !Array.isArray(v.structural.gate.rules) ||
    !Array.isArray(v.structural.gate.differences) ||
    !record(v.structural.report) ||
    !record(v.structural.report.quality) ||
    !record(v.structural.report.quality.metrics)
  )
    return invalid();
  const metrics = v.structural.report.quality.metrics;
  if (
    !Object.values(metrics).every(
      (m) =>
        record(m) &&
        (m.value === null ||
          (typeof m.value === "number" && m.value >= 0 && m.value <= 1)),
    )
  )
    return invalid();
  if (
    !v.structural.gate.rules.every(
      (r) =>
        record(r) &&
        typeof r.name === "string" &&
        typeof r.status === "string" &&
        typeof r.expected === "string" &&
        typeof r.actual === "string",
    )
  )
    return invalid();
  return v as Artifact;
}
export function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 404) return "记录不存在，或当前账号没有访问权限。";
    if (error.status === 409)
      return "输入与已保存状态冲突，或语料有效期不足。请重新校验。";
    if (error.status === 422)
      return "输入格式不符合评测协议，请核对 Dataset、Scenario 和预算。";
    if (error.status === 401) return "登录状态需要恢复，请重新登录。";
  }
  return "服务或连接异常，请重试。已创建的任务会继续执行。";
}
