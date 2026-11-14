import type { ErrorCode } from "./generated/run-event-v1";
import type { ChatRun } from "./chat";
import type { RunProjection } from "./events/runEventReducer";
import { isEventValue } from "./events/runEventContract";

const reasons: Record<ErrorCode, string> = {
  PROVIDER_ERROR: "模型服务调用失败，本次回答未完成。",
  PROVIDER_TIMEOUT: "等待模型服务超时，本次回答未完成。",
  PROVIDER_INVALID_RESPONSE: "模型回答未通过结构或引用校验，不能作为完整结果。",
  EXECUTION_EXPIRED: "本次任务的执行资格已过期，未能完成回答。",
  EXECUTOR_INTERRUPTED: "服务执行中断，本次回答未完成。",
  MODEL_SNAPSHOT_MISSING: "本次任务缺少冻结的模型配置。",
  MODEL_SNAPSHOT_INVALID: "本次任务的冻结模型配置无效。",
  CONTEXT_INVALID: "当前问题或对话上下文无法安全提交给模型。",
  EXECUTION_STORAGE_UNAVAILABLE: "执行期间无法访问持久存储，本次回答未完成。",
  TOOL_NOT_AVAILABLE: "模型请求了本次任务未提供的工具。",
  TOOL_INVALID_ARGUMENTS: "模型生成的工具参数不符合要求。",
  TOOL_PERMISSION_DENIED: "当前账号或本次任务没有执行该工具的权限。",
  TOOL_EXECUTION_FAILED: "工具执行失败，本次回答未完成。",
  TOOL_INVALID_RESULT: "工具结果未通过校验，无法安全用于回答。",
  AGENT_BUDGET_EXCEEDED: "本次任务已达到模型、工具或检索次数上限。",
  AGENT_DEADLINE_EXCEEDED: "本次任务已达到总执行时限。",
  KNOWLEDGE_ENCODING_FAILED: "问题编码失败，暂时无法检索知识库。",
  KNOWLEDGE_RETRIEVAL_FAILED: "知识库检索服务调用失败，并不表示资料为空。",
  KNOWLEDGE_SCOPE_INVALID: "本次知识库检索范围或索引状态无效。",
  KNOWLEDGE_RESULT_INVALID:
    "检索资料的来源或结构未通过校验，无法安全用于回答。",
  KNOWLEDGE_QUERY_TOO_LONG: "检索问题超过当前编码模型的输入长度限制。",
  HITL_EXPIRED: "补充信息的等待期限已结束，原任务无法继续。",
  HITL_CHECKPOINT_INVALID: "原任务的恢复状态未通过校验，无法安全继续。",
};

export function errorPresentation(code: unknown) {
  const known = isEventValue(code, "ErrorCode");
  const safeCode = known ? (code as ErrorCode) : null;
  let action =
    "确认服务和资料状态后，可重新提问发起新任务；同步历史不会重新执行旧任务。";
  if (safeCode === "CONTEXT_INVALID" || safeCode === "KNOWLEDGE_QUERY_TOO_LONG")
    action = "请缩短问题，必要时新建对话后再提问。";
  else if (safeCode === "TOOL_PERMISSION_DENIED")
    action = "请检查当前账号及资料权限，不要通过重复提交绕过权限检查。";
  else if (safeCode?.startsWith("MODEL_SNAPSHOT"))
    action =
      "请先检查模型配置，再重新提问；已创建任务的模型快照不会被自动改写。";
  else if (!known)
    action = "请同步历史；若仍无法确认原因，请提供本次任务编号协助排查。";
  return {
    code: safeCode ?? "UNKNOWN_ERROR",
    reason: safeCode
      ? reasons[safeCode]
      : "本次执行未能完成，错误类型暂时无法识别。",
    action,
  };
}
export type FailureView = ReturnType<typeof errorPresentation>;

export function failureForRun(
  run: ChatRun,
  projection: RunProjection,
): FailureView | null {
  if (["succeeded", "cancelled"].includes(run.status)) return null;
  if (run.status === "failed") return errorPresentation(run.error_code);
  return projection.runStatus === "failed"
    ? errorPresentation(projection.errorCode)
    : null;
}
