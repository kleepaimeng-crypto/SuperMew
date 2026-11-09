import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";
import { useChatStore } from "./chat";
import {
  body,
  cancelled,
  eventResponse,
  ids,
  messages,
  run,
  started,
  success,
  time,
} from "../../tests/fixtures/chat";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
let chat: ReturnType<typeof useChatStore>;
let auth: ReturnType<typeof useAuthStore>;
beforeEach(() => {
  setActivePinia(createPinia());
  vi.stubGlobal("sessionStorage", {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
  auth = useAuthStore();
  auth.user = { id: "user-a", username: "alice", created_at: time };
  auth.status = "authenticated";
  auth.accessToken = "old-token";
  chat = useChatStore();
});
afterEach(() => {
  chat.close();
  vi.unstubAllGlobals();
});

it("从历史的 pending 消息重放，完整正文替换且不会重复创建消息", async () => {
  let complete = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.includes("/messages"))
        return json({
          items: complete
            ? messages.map((m) =>
                m.role === "assistant"
                  ? { ...m, status: "completed", content: "今天天气很好" }
                  : m,
              )
            : messages,
          previous_cursor: null,
        });
      if (path.includes("/events/stream")) {
        complete = true;
        return eventResponse(success);
      }
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(() =>
    expect(chat.runs[ids.run]?.projection.runStatus).toBe("succeeded"),
  );
  expect(chat.displayMessages).toHaveLength(2);
  expect(chat.displayMessages[1]!.content).toBe("今天天气很好");
});

it("断线保留游标，重连重放重复片段不重复追加", async () => {
  const cursors: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.includes("/events/stream")) {
        cursors.push(path);
        return eventResponse(
          cursors.length === 1
            ? [...started, body]
            : [body, ...success.slice(3)],
        );
      }
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(
    () => expect(chat.runs[ids.run]?.projection.runStatus).toBe("succeeded"),
    { timeout: 2000 },
  );
  expect(cursors[0]).toContain("after=0");
  expect(cursors[1]).toContain("after=3");
  expect(chat.displayMessages[1]!.content).toBe("今天天气很好");
});

it("跳号不推进游标，重连从原位置补读", async () => {
  const cursors: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.includes("/events/stream")) {
        cursors.push(path);
        return eventResponse(
          cursors.length === 1 ? [...started, success[4]!] : success.slice(2),
        );
      }
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(
    () => expect(chat.runs[ids.run]?.projection.runStatus).toBe("succeeded"),
    { timeout: 2000 },
  );
  expect(cursors[1]).toContain("after=2");
});

it("401 刷新后保留投影与原游标，并使用新 Access Token", async () => {
  let attempts = 0;
  const authHeaders: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options: RequestInit) => {
      if (path === "/auth/refresh")
        return json({
          access_token: "new-token",
          token_type: "bearer",
          expires_in: 900,
        });
      if (path === "/auth/me") return json(auth.user);
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.includes("/events/stream")) {
        authHeaders.push(
          (options.headers as Record<string, string>).Authorization,
        );
        ++attempts;
        if (attempts === 1) return eventResponse([...started, body]);
        if (attempts === 2) return new Response(null, { status: 401 });
        expect(path).toContain("after=3");
        return eventResponse(success.slice(3));
      }
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(
    () => expect(chat.runs[ids.run]?.projection.runStatus).toBe("succeeded"),
    { timeout: 2000 },
  );
  expect(authHeaders).toEqual([
    "Bearer old-token",
    "Bearer old-token",
    "Bearer new-token",
  ]);
  expect(auth.user?.id).toBe("user-a");
});

it("已提交 Run 后历史读取失败只重试历史，不重发问题", async () => {
  let submitted = false;
  let posts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options: RequestInit) => {
      if (path.endsWith("/runs") && options.method === "POST") {
        submitted = true;
        ++posts;
        return json({ ...run, status: "pending" }, 201);
      }
      if (path.includes("/messages"))
        return submitted
          ? json({}, 503)
          : json({ items: [], previous_cursor: null });
      if (path.includes("/events/stream")) return eventResponse(success);
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  chat.draft = "你好";
  expect(await chat.submit()).toBe(true);
  expect(chat.acceptedRunId).toBe(ids.run);
  expect(chat.pending).toBeNull();
  expect(chat.historyError).toContain("加载失败");
  await chat.loadHistory();
  expect(posts).toBe(1);
});

it("未知提交保留正文与 key，重试使用同一意图且双击不重复 POST", async () => {
  const payloads: unknown[] = [];
  let release!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options: RequestInit) => {
      if (path.endsWith("/runs") && options.method === "POST") {
        payloads.push(JSON.parse(String(options.body)));
        if (payloads.length === 1) throw new TypeError("Network error");
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      if (path.includes("/messages"))
        return json({ items: [], previous_cursor: null });
      if (path.includes("/events/stream")) return eventResponse(success);
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  chat.draft = "原问题";
  expect(await chat.submit()).toBe(false);
  expect(chat.pending?.content).toBe("原问题");
  chat.draft = "后来输入";
  const retry = chat.submit();
  expect(await chat.submit()).toBe(false);
  release(json(run));
  await retry;
  expect(payloads[0]).toEqual(payloads[1]);
  expect((payloads[1] as { content: string }).content).toBe("原问题");
});

it("切换对话时迟到的历史响应不能覆盖新对话", async () => {
  let release!: (value: Response) => void;
  const other = "00000000-0000-0000-0000-000000000099";
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      path.includes(ids.thread)
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : Promise.resolve(json({ items: [], previous_cursor: null })),
    ),
  );
  const old = chat.open(ids.thread);
  await chat.open(other);
  release(json({ items: messages, previous_cursor: null }));
  await old;
  expect(chat.threadId).toBe(other);
  expect(chat.messages).toEqual([]);
});

it("退出或切换账号时清理视图，迟到响应不能恢复旧数据", async () => {
  let release!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    ),
  );
  const old = chat.open(ids.thread);
  auth.user = { id: "user-b", username: "bob", created_at: time };
  release(json({ items: messages, previous_cursor: null }));
  await old;
  expect(chat.threadId).toBeNull();
  expect(chat.messages).toEqual([]);
});

it("取消接口 202 不提前写终态，只有已提交取消事件改变投影", async () => {
  let cancelledRequest = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options: RequestInit) => {
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.endsWith("/cancel")) {
        cancelledRequest = true;
        return json({ ...run, cancel_requested_at: time }, 202);
      }
      if (path.includes("/events/stream"))
        return eventResponse(cancelledRequest ? cancelled : [...started, body]);
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(() => expect(chat.runs[ids.run]?.projection.cursor).toBe(3));
  await chat.cancel(ids.run);
  expect(chat.statusLabel(ids.run)).toBe("取消中");
  expect(chat.runs[ids.run]!.projection.runStatus).toBe("running");
  await vi.waitFor(
    () => expect(chat.runs[ids.run]?.projection.runStatus).toBe("cancelled"),
    { timeout: 2000 },
  );
  expect(chat.displayMessages[1]!.content).toBe("今天");
});

it("取消 503 不改 Run 状态，可通过查询确认原任务", async () => {
  let cancelledOnServer = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options: RequestInit) => {
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.endsWith("/cancel")) {
        cancelledOnServer = true;
        return json({}, 503);
      }
      if (path.includes("/events/stream"))
        return eventResponse(
          [...started, body],
          options.signal as AbortSignal,
          true,
        );
      return json(
        cancelledOnServer
          ? {
              ...run,
              status: "cancelled",
              cancel_requested_at: time,
              finished_at: time,
            }
          : run,
      );
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(() => expect(chat.runs[ids.run]?.projection.cursor).toBe(3));
  await chat.cancel(ids.run);
  expect(chat.runStatus(chat.runs[ids.run]!)).toBe("running");
  expect(chat.runs[ids.run]!.cancelError).toContain("未确认");
  await chat.synchronizeRun(ids.run);
  expect(chat.runStatus(chat.runs[ids.run]!)).toBe("cancelled");
});

it("204 或非法事件不能伪造完成，保留原投影并提供恢复入口", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.includes("/events/stream"))
        return new Response(null, { status: 204 });
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(() => expect(chat.runs[ids.run]?.connection).toBe("error"));
  expect(chat.runs[ids.run]!.projection.cursor).toBe(0);
  expect(chat.runStatus(chat.runs[ids.run]!)).toBe("running");
});

it("取消响应丢失后收到取消终态，清除结果未确认提示", async () => {
  let cancelledOnServer = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.includes("/messages"))
        return json({ items: messages, previous_cursor: null });
      if (path.endsWith("/cancel")) {
        cancelledOnServer = true;
        return json({}, 503);
      }
      if (path.includes("/events/stream"))
        return eventResponse(
          cancelledOnServer ? cancelled : [...started, body],
        );
      return json(run);
    }),
  );
  await chat.open(ids.thread);
  await vi.waitFor(() => expect(chat.runs[ids.run]?.projection.cursor).toBe(3));
  await chat.cancel(ids.run);
  expect(chat.runs[ids.run]!.cancelError).toContain("未确认");
  await vi.waitFor(
    () => expect(chat.runs[ids.run]?.projection.runStatus).toBe("cancelled"),
    { timeout: 2000 },
  );
  expect(chat.runs[ids.run]!.cancelError).toBe("");
});
