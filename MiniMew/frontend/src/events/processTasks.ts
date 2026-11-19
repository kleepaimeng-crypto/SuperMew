import type {
  ProcessStepData,
  ProcessDetailData,
  RunStatus,
} from "../generated/run-event-v1";

export interface ProcessTask {
  step: ProcessStepData;
  detail?: ProcessDetailData;
  children: ProcessTask[];
  records: ProcessStepData[];
  details: readonly ProcessDetailData[];
  number: number;
}
export const stageNames: Record<string, string> = {
  model: "模型请求",
  retrieval: "检索资料",
  dense: "向量召回",
  bm25: "关键词召回",
  fusion: "融合排序",
  merge: "同节补全",
  rerank: "重排候选",
  assessment: "材料检查",
  rewrite: "调整检索问题",
  plan: "拆解问题",
  subquestion: "子问题",
};
export const outcomes: Record<string, string> = {
  sufficient: "材料充分",
  insufficient: "材料仍有缺口",
  ambiguous: "需要补充信息",
  conflicting: "材料存在冲突",
  no_documents: "当前范围没有已发布文档",
};
export function processTasks(
  steps: readonly ProcessStepData[],
  details: readonly ProcessDetailData[],
): ProcessTask[] {
  const ids = new Set(steps.map((s) => s.step_id));
  function descendants(id: string): ProcessStepData[] {
    return steps
      .filter((s) => s.parent_step_id === id)
      .flatMap((s) => [s, ...descendants(s.step_id)]);
  }
  const roots = steps.filter(
    (s) => !s.parent_step_id || !ids.has(s.parent_step_id),
  );
  const make = (s: ProcessStepData): ProcessTask => ({
    step: s,
    detail: details.find((d) => d.step_id === s.step_id),
    children: [],
    records: descendants(s.step_id),
    details,
    number: 0,
  });
  const tasks = roots.map(make);
  const assigned = new Set<string>();
  for (const task of tasks.filter((t) => t.step.kind === "plan")) {
    task.children = tasks.filter(
      (t) =>
        t.step.kind === "subquestion" &&
        t.step.tool_call_id === task.step.tool_call_id,
    );
    task.children.forEach((child, i) => {
      const index = task.detail?.questions.indexOf(child.step.query) ?? -1;
      child.number = index >= 0 ? index + 1 : i + 1;
      assigned.add(child.step.step_id);
    });
    task.children.sort((a, b) => a.number - b.number);
  }
  return tasks.filter((t) => !assigned.has(t.step.step_id));
}
export function taskTitle(task: ProcessTask): string {
  const { step: s, detail: d } = task;
  if (s.kind === "retrieval")
    return (
      (
        {
          initial: "首次检索资料",
          rewritten: "调整问题后补查",
          clarified: "根据补充信息查询",
          child: "查询子问题资料",
        } as Record<string, string>
      )[d?.phase ?? ""] ?? "检索资料"
    );
  if (s.kind === "assessment")
    return (
      (
        {
          initial: "检查首次检索材料",
          aggregate: "合并材料后再次检查",
          rewritten: "检查补查结果",
          clarified: "检查补充后的材料",
        } as Record<string, string>
      )[d?.phase ?? ""] ?? "检查材料"
    );
  if (s.kind === "plan")
    return s.count ? `补查 ${s.count} 个方向` : "拆解问题，准备补查";
  if (s.kind === "subquestion") return `子问题 ${task.number || ""}`.trim();
  if (s.kind === "model") {
    const title =
      (
        {
          answer: "组织最终回答",
          answer_repair: "校正回答格式",
          tool_selection: "确定下一步工具调用",
          model_decision: "处理问题",
        } as Record<string, string>
      )[d?.purpose ?? ""] ?? "模型请求";
    return d && d.attempt > 1 ? `${title}（第${d.attempt}次尝试）` : title;
  }
  return stageNames[s.kind] ?? s.kind;
}
export function taskState(task: ProcessTask, runStatus?: RunStatus): string {
  const members = [task.step, ...task.children.map((c) => c.step)];
  if (members.some((s) => s.status === "running")) {
    if (runStatus === "waiting_input") return "等待补充";
    if (runStatus === "cancelled") return "已取消";
    if (runStatus === "failed" || runStatus === "succeeded") return "执行中断";
    return "进行中";
  }
  if (task.step.status === "failed") return "未完成";
  if (members.some((s) => s.status === "failed")) return "部分未完成";
  if (
    task.step.kind === "plan" &&
    task.detail?.questions.length &&
    task.children.length < task.detail.questions.length
  )
    return runStatus === "failed" ||
      runStatus === "cancelled" ||
      runStatus === "succeeded"
      ? "未全部执行"
      : "正在安排补查";
  return task.step.status === "completed" ? "已完成" : "进行中";
}
export function materialCount(s: ProcessStepData): string {
  if (["dense", "bm25", "fusion", "merge", "rerank"].includes(s.kind))
    return `${s.count}个候选片段`;
  if (s.kind === "retrieval") return `保留${s.count}个材料片段`;
  if (s.kind === "assessment" || s.kind === "subquestion")
    return `检查${s.count}个材料片段`;
  return "";
}
export function elapsed(ms: number): string {
  return ms < 100 ? "<0.1秒" : `${(ms / 1000).toFixed(1)}秒`;
}
