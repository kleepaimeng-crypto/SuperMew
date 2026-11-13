import { expect, it } from "vitest";
import type { RunEvent } from "../generated/run-event-v1";
import {
  createRunProjection,
  reduceRunEvent,
  type RunBinding,
  type RunProjection,
} from "./runEventReducer";

const binding: RunBinding = {
  threadId: "00000000-0000-0000-0000-000000000001",
  runId: "00000000-0000-0000-0000-000000000002",
  assistantMessageId: "00000000-0000-0000-0000-000000000003",
};
const other: RunBinding = {
  threadId: "00000000-0000-0000-0000-000000000004",
  runId: "00000000-0000-0000-0000-000000000005",
  assistantMessageId: "00000000-0000-0000-0000-000000000006",
};
const timestamp = "2026-11-06T00:00:00Z";

function event<T extends RunEvent["type"]>(
  type: T,
  sequence: number,
  data: Extract<RunEvent, { type: T }>["data"],
  target = binding,
): Extract<RunEvent, { type: T }> {
  return {
    schema_version: 1,
    event_id: `10000000-0000-0000-0000-${String(sequence).padStart(12, "0")}`,
    thread_id: target.threadId,
    run_id: target.runId,
    sequence,
    timestamp,
    type,
    data,
  } as Extract<RunEvent, { type: T }>;
}

function apply(state: RunProjection, input: RunEvent): RunProjection {
  Object.freeze(state);
  const result = reduceRunEvent(state, input);
  expect(result.kind).toBe("applied");
  expect(result.state).not.toBe(state);
  return result.state;
}

function created(target = binding): RunProjection {
  return apply(
    createRunProjection(target),
    event(
      "run.created",
      1,
      { status: "pending", assistant_message_id: target.assistantMessageId },
      target,
    ),
  );
}

function running(target = binding): RunProjection {
  return apply(
    created(target),
    event("run.started", 2, { status: "running" }, target),
  );
}

function delta(sequence: number, content = "今天", target = binding) {
  return event(
    "message.delta",
    sequence,
    { message_id: target.assistantMessageId, content },
    target,
  );
}

function messageCompleted(
  sequence: number,
  status: "completed" | "failed" | "cancelled" = "completed",
  content = "今天天气很好",
) {
  return event("message.completed", sequence, {
    message_id: binding.assistantMessageId,
    status,
    content,
  });
}

function finished(status: "succeeded" | "failed" | "cancelled") {
  let state = running();
  if (status === "cancelled") {
    state = apply(
      state,
      event("run.cancel_requested", 3, { cancel_requested_at: timestamp }),
    );
    state = apply(state, messageCompleted(4, "cancelled", ""));
    const terminal = event("run.cancelled", 5, { status: "cancelled" });
    return { state: apply(state, terminal), terminal };
  }
  state = apply(
    state,
    messageCompleted(3, status === "succeeded" ? "completed" : "failed"),
  );
  const terminal =
    status === "succeeded"
      ? event("run.completed", 4, { status: "succeeded" })
      : event("run.failed", 4, {
          status: "failed",
          error_code: "PROVIDER_TIMEOUT",
        });
  return { state: apply(state, terminal), terminal };
}

it("创建投影从事件起点开始，不持有共享可变状态", () => {
  const source = { ...binding };
  const first = createRunProjection(source);
  const second = createRunProjection(binding);
  source.runId = other.runId;
  expect(first).toEqual({
    ...binding,
    cursor: 0,
    content: "",
    messageStatus: "pending",
    runStatus: "pending",
    cancelRequestedAt: null,
    errorCode: null,
    tools: [],
  });
  expect(first).toEqual(second);
  expect(first).not.toBe(second);
});

it("逐批追加并用完整正文替换，消息完成后仍等 Run 终态", () => {
  const initial = running();
  const first = apply(initial, delta(3, "今"));
  const second = apply(first, delta(4, "天"));
  expect(initial.content).toBe("");
  expect(first.content).toBe("今");
  expect(second.content).toBe("今天");
  const completed = apply(second, messageCompleted(5));
  expect(completed.content).toBe("今天天气很好");
  expect(completed.messageStatus).toBe("completed");
  expect(completed.runStatus).toBe("running");
  expect(completed.cursor).toBe(5);
  const terminal = apply(
    completed,
    event("run.completed", 6, { status: "succeeded" }),
  );
  expect(terminal.runStatus).toBe("succeeded");
  expect(terminal.errorCode).toBeNull();
  expect(completed.runStatus).toBe("running");
});

it("重放正文或更早事件不重复追加，也不回退游标", () => {
  const input = delta(3);
  const state = apply(running(), input);
  for (const duplicate of [
    input,
    event("run.started", 2, { status: "running" }),
  ]) {
    const result = reduceRunEvent(state, duplicate);
    expect(result.kind).toBe("duplicate");
    expect(result.state).toBe(state);
    expect(result.state.content).toBe("今天");
    expect(result.state.cursor).toBe(3);
  }
});

it("发现缺口保留原状态和游标，补齐后再处理后续事件", () => {
  const state = apply(running(), delta(3, "今"));
  const fifth = delta(5, "天气");
  const gap = reduceRunEvent(state, fifth);
  expect(gap).toEqual({ kind: "gap", state, after: 3 });
  expect(gap.state).toBe(state);
  expect(state.content).toBe("今");
  const fourth = apply(state, delta(4, "天"));
  const resumed = apply(fourth, fifth);
  expect(resumed.cursor).toBe(5);
  expect(resumed.content).toBe("今天天气");
});

it("拒绝错误归属后仍从原位置恢复，同序号的正确事件仍能应用", () => {
  const state = apply(running(), delta(3, "今"));
  const wrong = event("message.delta", 4, {
    message_id: other.assistantMessageId,
    content: "错误正文",
  });
  const rejected = reduceRunEvent(state, wrong);
  expect(rejected.kind).toBe("foreign");
  expect(rejected.state).toBe(state);
  expect(rejected.state.cursor).toBe(3);
  expect(apply(rejected.state, delta(4, "天")).content).toBe("今天");
});

it.each(["thread", "run", "message", "created-message"] as const)(
  "拒绝 %s 归属不符的事件，不因其较大序号触发补读",
  (field) => {
    const input = delta(20);
    const foreign: RunEvent =
      field === "created-message"
        ? event("run.created", 20, {
            status: "pending",
            assistant_message_id: other.assistantMessageId,
          })
        : {
            ...input,
            thread_id: field === "thread" ? other.threadId : binding.threadId,
            run_id: field === "run" ? other.runId : binding.runId,
            data: {
              ...input.data,
              message_id:
                field === "message"
                  ? other.assistantMessageId
                  : binding.assistantMessageId,
            },
          };
    const state = running();
    const result = reduceRunEvent(state, foreign);
    expect(result.kind).toBe("foreign");
    expect(result.state).toBe(state);
  },
);

it("两个并行 Run 使用相同序号时正文和游标仍互相独立", () => {
  const first = running();
  const second = running(other);
  const firstUpdated = apply(first, delta(3, "A"));
  const secondUpdated = apply(second, delta(3, "B", other));
  expect(firstUpdated.content).toBe("A");
  expect(secondUpdated.content).toBe("B");
  expect(first.content).toBe("");
  expect(second.content).toBe("");
  expect(firstUpdated.cursor).toBe(3);
  expect(secondUpdated.cursor).toBe(3);
  expect(reduceRunEvent(secondUpdated, delta(4, "A")).kind).toBe("foreign");
  expect(secondUpdated.content).toBe("B");
});

it.each(["", "今天"])("失败使用已提交最终正文 %j 并记录稳定错误", (content) => {
  let state = running();
  if (content) state = apply(state, delta(3, content));
  state = apply(state, messageCompleted(state.cursor + 1, "failed", content));
  expect(state.runStatus).toBe("running");
  const result = apply(
    state,
    event("run.failed", state.cursor + 1, {
      status: "failed",
      error_code: "PROVIDER_TIMEOUT",
    }),
  );
  expect(result.content).toBe(content);
  expect(result.messageStatus).toBe("failed");
  expect(result.runStatus).toBe("failed");
  expect(result.errorCode).toBe("PROVIDER_TIMEOUT");
});

it("running 的取消请求先保留正文和 Run 状态，直到取消终态到达", () => {
  const body = apply(running(), delta(3));
  const requested = apply(
    body,
    event("run.cancel_requested", 4, { cancel_requested_at: timestamp }),
  );
  expect(requested.runStatus).toBe("running");
  expect(requested.messageStatus).toBe("pending");
  expect(requested.cancelRequestedAt).toBe(timestamp);
  expect(requested.content).toBe("今天");
  const message = apply(requested, messageCompleted(5, "cancelled", "今天"));
  expect(message.runStatus).toBe("running");
  const result = apply(
    message,
    event("run.cancelled", 6, { status: "cancelled" }),
  );
  expect(result.runStatus).toBe("cancelled");
  expect(result.content).toBe("今天");
  expect(result.errorCode).toBeNull();
});

it("pending 无 started 事件也能走完直接取消路径", () => {
  const requested = apply(
    created(),
    event("run.cancel_requested", 2, { cancel_requested_at: timestamp }),
  );
  const message = apply(requested, messageCompleted(3, "cancelled", ""));
  expect(message.runStatus).toBe("pending");
  const result = apply(
    message,
    event("run.cancelled", 4, { status: "cancelled" }),
  );
  expect(result.runStatus).toBe("cancelled");
  expect(result.content).toBe("");
  expect(result.cursor).toBe(4);
});

it.each(["succeeded", "failed", "cancelled"] as const)(
  "%s 终态允许重复重放，但拒绝迟到正文、缺号和状态回退",
  (status) => {
    const { state, terminal } = finished(status);
    const repeated = reduceRunEvent(state, terminal);
    expect(repeated.kind).toBe("duplicate");
    expect(repeated.state).toBe(state);
    for (const input of [
      delta(state.cursor + 1, "迟到"),
      delta(state.cursor + 2, "缺号"),
      event("run.started", state.cursor + 1, { status: "running" }),
    ]) {
      const result = reduceRunEvent(state, input);
      expect(result.kind).toBe("invalid");
      expect(result.state).toBe(state);
      expect(result.state.runStatus).toBe(status);
    }
  },
);

it.each([0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
  "拒绝不能安全表示的事件序号 %s，不推进游标",
  (sequence) => {
    const state = running();
    const result = reduceRunEvent(state, delta(sequence));
    expect(result.kind).toBe("invalid");
    expect(result.state).toBe(state);
  },
);

it("必须从 run.created 重放，不能把 started 当成初始事件", () => {
  const state = createRunProjection(binding);
  const result = reduceRunEvent(
    state,
    event("run.started", 1, { status: "running" }),
  );
  expect(result.kind).toBe("invalid");
  expect(result.state).toBe(state);
  expect(result.state.cursor).toBe(0);
});

it("错误状态转换不推进游标，随后同序号的正确事件可继续", () => {
  const state = created();
  const rejected = reduceRunEvent(state, delta(2));
  expect(rejected.kind).toBe("invalid");
  expect(rejected.state).toBe(state);
  expect(rejected.state.cursor).toBe(1);
  expect(
    apply(rejected.state, event("run.started", 2, { status: "running" }))
      .runStatus,
  ).toBe("running");
});

it("消息完成后的正文、取消请求和重复完成均不应用", () => {
  const state = apply(running(), messageCompleted(3));
  for (const input of [
    delta(4, "迟到"),
    event("run.cancel_requested", 4, { cancel_requested_at: timestamp }),
    messageCompleted(4),
  ]) {
    const result = reduceRunEvent(state, input);
    expect(result.kind).toBe("invalid");
    expect(result.state).toBe(state);
  }
});

it("取消请求后拒绝正文、重新开始和成功消息", () => {
  const state = apply(
    running(),
    event("run.cancel_requested", 3, { cancel_requested_at: timestamp }),
  );
  for (const input of [
    delta(4),
    event("run.started", 4, { status: "running" }),
    messageCompleted(4),
    messageCompleted(4, "failed"),
    event("run.cancel_requested", 4, { cancel_requested_at: timestamp }),
  ]) {
    const result = reduceRunEvent(state, input);
    expect(result.kind).toBe("invalid");
    expect(result.state).toBe(state);
  }
});

it("缺少匹配消息状态或取消请求时不能应用 Run 终态", () => {
  const state = running();
  const failedMessage = apply(state, messageCompleted(3, "failed"));
  for (const [current, input] of [
    [state, event("run.completed", 3, { status: "succeeded" })],
    [state, event("run.cancelled", 3, { status: "cancelled" })],
    [state, messageCompleted(3, "cancelled")],
    [failedMessage, event("run.completed", 4, { status: "succeeded" })],
    [
      apply(state, messageCompleted(3)),
      event("run.failed", 4, {
        status: "failed",
        error_code: "PROVIDER_ERROR",
      }),
    ],
  ] as const) {
    const result = reduceRunEvent(current, input);
    expect(result.kind).toBe("invalid");
    expect(result.state).toBe(current);
  }
});
