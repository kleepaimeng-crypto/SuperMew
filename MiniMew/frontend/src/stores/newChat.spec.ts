import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";
import { useNewChatStore } from "./newChat";
import { PERSONAL_SCOPE, useWorkspaceStore } from "./workspace";

const createdPersonal = {
  id: "00000000-0000-0000-0000-000000000031",
  title: "新对话",
  created_at: "2026-11-20T00:00:00Z",
};
const projectId = "11111111-1111-4111-8111-111111111111";
const createdProject = {
  id: "00000000-0000-0000-0000-000000000032",
  project_id: projectId,
  title: "新对话",
  created_at: "2026-11-20T00:00:00Z",
};
const createdRun = {
  id: "00000000-0000-0000-0000-000000000091",
  thread_id: createdPersonal.id,
  user_message_id: "00000000-0000-0000-0000-000000000092",
  assistant_message_id: "00000000-0000-0000-0000-000000000093",
  status: "pending",
  created_at: "2026-11-20T00:00:01Z",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function signIn(userId = "user-a") {
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.accessToken = "token-a";
  auth.user = {
    id: userId,
    username: userId,
    created_at: "2026-11-20T00:00:00Z",
  };
  return auth;
}

function makeStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    clear: () => {
      map.clear();
    },
  };
}

describe("useNewChatStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.stubGlobal("sessionStorage", makeStorage());
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("空草稿不发起任何创建对话请求", async () => {
    signIn();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const store = useNewChatStore();
    store.draft = "   ";
    expect(await store.submitFirstMessage()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("首发消息创建 Thread 并提交 Run，完成后清空草稿并写入对应工作区桶", async () => {
    signIn();
    const calls: { url: string; method: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (url === "/v1/threads" && method === "POST")
        return Promise.resolve(jsonResponse(createdPersonal, 201));
      if (url === `/v1/threads/${createdPersonal.id}/runs` && method === "POST")
        return Promise.resolve(jsonResponse(createdRun, 201));
      if (url.startsWith("/v1/threads?"))
        return Promise.resolve(
          jsonResponse({ items: [createdPersonal], next_offset: null }),
        );
      return Promise.resolve(jsonResponse([]));
    });

    const store = useNewChatStore();
    const workspace = useWorkspaceStore();
    store.openScope(null);
    store.draft = "你好，帮我查资料";
    const result = await store.submitFirstMessage();
    expect(result).toEqual({ threadId: createdPersonal.id, submitted: true });
    expect(store.draft).toBe("");
    expect(store.pending).toBeNull();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      "/v1/threads",
      `/v1/threads/${createdPersonal.id}/runs`,
    ]);
    expect(workspace.buckets[PERSONAL_SCOPE]?.items[0]?.id).toBe(
      createdPersonal.id,
    );
    expect(
      sessionStorage.getItem(
        `minimew.chat-pending:user-a:${createdPersonal.id}`,
      ),
    ).toBeNull();
  });

  it("阶段A创建Thread失败保留草稿与错误提示，不自动重复创建", async () => {
    signIn();
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (String(input) === "/v1/threads" && init?.method === "POST") {
        return Promise.resolve(jsonResponse({}, 500));
      }
      return Promise.resolve(jsonResponse([]));
    });

    const store = useNewChatStore();
    store.openScope(null);
    store.draft = "网络波动测试";
    expect(await store.submitFirstMessage()).toBeNull();
    expect(store.pending?.content).toBe("网络波动测试");
    expect(store.submitError).toContain("创建对话结果未确认");
    expect(
      sessionStorage.getItem(
        `minimew.new-chat-pending:user-a:${PERSONAL_SCOPE}`,
      ),
    ).toContain("网络波动测试");
  });

  it("阶段A成功但阶段B提交Run失败时，将原幂等意图转交目标Thread恢复，避免重复建Thread", async () => {
    signIn();
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url === "/v1/threads" && init?.method === "POST") {
        return Promise.resolve(jsonResponse(createdProject, 201));
      }
      if (url === `/v1/threads/${createdProject.id}/runs`) {
        return Promise.reject(new TypeError("network down"));
      }
      if (url.startsWith("/v1/threads?")) {
        return Promise.resolve(
          jsonResponse({ items: [createdProject], next_offset: null }),
        );
      }
      return Promise.resolve(jsonResponse([]));
    });

    const store = useNewChatStore();
    store.openScope(projectId);
    store.draft = "项目里的第一条问题";
    const result = await store.submitFirstMessage();
    expect(result).toEqual({ threadId: createdProject.id, submitted: false });
    // new-chat-pending 已清理，意图已转交给目标 thread 的 chat-pending
    expect(
      sessionStorage.getItem(`minimew.new-chat-pending:user-a:${projectId}`),
    ).toBeNull();
    const handoff = sessionStorage.getItem(
      `minimew.chat-pending:user-a:${createdProject.id}`,
    );
    expect(handoff).toContain("项目里的第一条问题");
  });
});
