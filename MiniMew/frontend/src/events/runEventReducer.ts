import type {
  ProcessStepData,
  ErrorCode,
  MessageStatus,
  RunEvent,
  RunStatus,
} from "../generated/run-event-v1";

export interface RunBinding {
  readonly threadId: string;
  readonly runId: string;
  readonly assistantMessageId: string;
}

export interface RunProjection extends RunBinding {
  readonly cursor: number;
  readonly content: string;
  readonly messageStatus: MessageStatus;
  readonly runStatus: RunStatus;
  readonly cancelRequestedAt: string | null;
  readonly errorCode: ErrorCode | null;
  readonly tools: readonly ToolProjection[];
  readonly steps: readonly ProcessStepData[];
  readonly waiting: {
    readonly checkpointId: string;
    readonly prompt: string;
    readonly expiresAt: string;
  } | null;
}

export interface ToolProjection {
  readonly id: string;
  readonly name: string;
  readonly status:
    "running" | "completed" | "failed" | "cancelled" | "interrupted";
  readonly errorCode: ErrorCode | null;
}

function settleTools(
  state: RunProjection,
  status: "cancelled" | "interrupted",
) {
  return state.tools.map((tool) =>
    tool.status === "running" ? { ...tool, status } : tool,
  );
}

export type EventReduction =
  | {
      readonly kind: "applied" | "duplicate" | "foreign" | "invalid";
      readonly state: RunProjection;
    }
  | {
      readonly kind: "gap";
      readonly state: RunProjection;
      readonly after: number;
    };

// 从 Journal 起点重放；历史快照的装载由后续历史/订阅层确定，不猜测事件游标。
export function createRunProjection(binding: RunBinding): RunProjection {
  return {
    threadId: binding.threadId,
    runId: binding.runId,
    assistantMessageId: binding.assistantMessageId,
    cursor: 0,
    content: "",
    messageStatus: "pending",
    runStatus: "pending",
    cancelRequestedAt: null,
    errorCode: null,
    tools: [],
    steps: [],
    waiting: null,
  };
}

// 输入已通过网络协议校验；这里只处理归属、连续性和业务状态转换。
export function reduceRunEvent(
  state: RunProjection,
  event: RunEvent,
): EventReduction {
  if (!belongsToRun(state, event)) return { kind: "foreign", state };
  if (!Number.isSafeInteger(event.sequence) || event.sequence < 1)
    return { kind: "invalid", state };
  if (event.sequence <= state.cursor) return { kind: "duplicate", state };
  if (
    state.runStatus === "succeeded" ||
    state.runStatus === "failed" ||
    state.runStatus === "cancelled"
  )
    return { kind: "invalid", state };
  if (event.sequence !== state.cursor + 1)
    return { kind: "gap", state, after: state.cursor };
  if (state.cursor === 0 && event.type !== "run.created")
    return { kind: "invalid", state };

  const next = transition(state, event);
  return next === null
    ? { kind: "invalid", state }
    : { kind: "applied", state: { ...next, cursor: event.sequence } };
}

function belongsToRun(state: RunProjection, event: RunEvent): boolean {
  if (event.thread_id !== state.threadId || event.run_id !== state.runId)
    return false;
  if (event.type === "run.created")
    return event.data.assistant_message_id === state.assistantMessageId;
  if (event.type === "message.delta" || event.type === "message.completed")
    return event.data.message_id === state.assistantMessageId;
  return true;
}

function transition(
  state: RunProjection,
  event: RunEvent,
): RunProjection | null {
  const pending = state.runStatus === "pending";
  const running = state.runStatus === "running";
  const messagePending = state.messageStatus === "pending";
  const waiting = state.runStatus === "waiting_input";
  const queuedResume = state.runStatus === "resume_pending";
  const cancelling = state.cancelRequestedAt !== null;

  switch (event.type) {
    case "process.step": {
      if (!running || !messagePending || cancelling) return null;
      const step = event.data;
      const old = state.steps.find((s) => s.step_id === step.step_id);
      if (
        step.tool_call_id &&
        !state.tools.some(
          (t) => t.id === step.tool_call_id && t.status === "running",
        )
      )
        return null;
      if (step.status === "running") {
        if (
          old ||
          (step.parent_step_id &&
            !state.steps.some(
              (p) =>
                p.step_id === step.parent_step_id &&
                p.status === "running" &&
                p.tool_call_id === step.tool_call_id,
            ))
        )
          return null;
        return { ...state, steps: [...state.steps, step] };
      }
      if (
        !old ||
        old.status !== "running" ||
        old.kind !== step.kind ||
        old.query !== step.query ||
        old.parent_step_id !== step.parent_step_id ||
        old.tool_call_id !== step.tool_call_id
      )
        return null;
      return {
        ...state,
        steps: state.steps.map((p) => (p.step_id === step.step_id ? step : p)),
      };
    }
    case "hitl.required":
      return running && messagePending && !cancelling && state.waiting === null
        ? {
            ...state,
            runStatus: "waiting_input",
            messageStatus: "waiting_input",
            waiting: {
              checkpointId: event.data.checkpoint_id,
              prompt: event.data.prompt,
              expiresAt: event.data.expires_at,
            },
          }
        : null;
    case "hitl.accepted":
      return waiting &&
        !cancelling &&
        state.waiting?.checkpointId === event.data.checkpoint_id
        ? { ...state, runStatus: "resume_pending", messageStatus: "pending" }
        : null;
    case "run.resumed":
      return queuedResume && messagePending && !cancelling
        ? { ...state, runStatus: "running" }
        : null;
    case "tool.started": {
      if (
        !running ||
        !messagePending ||
        cancelling ||
        state.tools.length >= 6 ||
        !event.data.tool_call_id.trim() ||
        state.tools.some((t) => t.id === event.data.tool_call_id)
      )
        return null;
      return {
        ...state,
        tools: [
          ...state.tools,
          {
            id: event.data.tool_call_id,
            name: event.data.tool_name,
            status: "running",
            errorCode: null,
          },
        ],
      };
    }
    case "tool.completed":
    case "tool.failed": {
      const tool = state.tools.find((t) => t.id === event.data.tool_call_id);
      if (
        !running ||
        !messagePending ||
        cancelling ||
        tool?.status !== "running" ||
        tool.name !== event.data.tool_name
      )
        return null;
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.id !== tool.id
            ? t
            : {
                ...t,
                status:
                  event.type === "tool.completed" ? "completed" : "failed",
                errorCode:
                  event.type === "tool.failed" ? event.data.error_code : null,
              },
        ),
      };
    }
    case "run.created":
      return state.cursor === 0 && pending && messagePending ? state : null;
    case "run.started":
      return pending && messagePending && !cancelling
        ? { ...state, runStatus: "running" }
        : null;
    case "message.delta":
      return running && messagePending && !cancelling
        ? { ...state, content: state.content + event.data.content }
        : null;
    case "message.completed": {
      if (!messagePending && !waiting) return null;
      const allowed =
        event.data.status === "cancelled"
          ? cancelling && (pending || running || waiting || queuedResume)
          : !cancelling &&
            (running ||
              ((waiting || queuedResume) && event.data.status === "failed"));
      return allowed
        ? {
            ...state,
            content: event.data.content,
            messageStatus: event.data.status,
          }
        : null;
    }
    case "run.completed":
      return running &&
        state.messageStatus === "completed" &&
        !cancelling &&
        state.tools.every((t) => t.status !== "running")
        ? { ...state, runStatus: "succeeded" }
        : null;
    case "run.failed":
      return (running || waiting || queuedResume) &&
        state.messageStatus === "failed" &&
        !cancelling
        ? {
            ...state,
            runStatus: "failed",
            errorCode: event.data.error_code,
            tools: settleTools(state, "interrupted"),
          }
        : null;
    case "run.cancel_requested":
      return (pending || running || waiting || queuedResume) &&
        (messagePending || waiting) &&
        !cancelling
        ? { ...state, cancelRequestedAt: event.data.cancel_requested_at }
        : null;
    case "run.cancelled":
      return cancelling && state.messageStatus === "cancelled"
        ? {
            ...state,
            runStatus: "cancelled",
            tools: settleTools(state, "cancelled"),
          }
        : null;
  }
  return null;
}
