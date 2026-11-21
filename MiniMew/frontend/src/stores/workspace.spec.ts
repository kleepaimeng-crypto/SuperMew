import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";
import { PERSONAL_SCOPE, useWorkspaceStore } from "./workspace";

const t1 = {
  archived: false,
  id: "00000000-0000-0000-0000-000000000001",
  title: "个人会话一",
  created_at: "2026-11-20T00:00:00Z",
};
const t2 = {
  archived: false,
  id: "00000000-0000-0000-0000-000000000002",
  title: "个人会话二",
  created_at: "2026-11-20T00:01:00Z",
};
const p1 = "11111111-1111-4111-8111-111111111111";
const pt1 = {
  archived: false,
  id: "00000000-0000-0000-0000-000000000010",
  project_id: p1,
  title: "项目会话一",
  created_at: "2026-11-20T00:02:00Z",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function signIn(userId = "user-a", token = "token-a") {
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.accessToken = token;
  auth.user = {
    id: userId,
    username: userId,
    created_at: "2026-11-20T00:00:00Z",
  };
  return auth;
}

describe("useWorkspaceStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("归档与恢复刷新两个列表，归档后的标题更新不会放回默认列表", async () => {
    signIn();
    let archived = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = new URL(String(input), "http://localhost");
      if (init?.method === "PATCH") {
        archived = JSON.parse(String(init.body)).archived;
        return Promise.resolve(jsonResponse({ ...t1, archived }));
      }
      return Promise.resolve(
        jsonResponse({
          items:
            (url.searchParams.get("archived") === "true") === archived
              ? [{ ...t1, archived }]
              : [],
          next_offset: null,
        }),
      );
    });
    const workspace = useWorkspaceStore();
    await workspace.loadScopeThreads(null, true);
    expect(workspace.buckets.personal!.items).toHaveLength(1);
    expect(await workspace.archiveThread(t1, true)).toBe(true);
    expect(workspace.buckets.personal!.items).toHaveLength(0);
    expect(workspace.buckets["personal:archived"]!.items[0]!.archived).toBe(
      true,
    );
    workspace.upsertThread({ ...t1, archived: true });
    expect(workspace.buckets.personal!.items).toHaveLength(0);
    expect(
      await workspace.archiveThread({ ...t1, archived: true }, false),
    ).toBe(true);
    expect(workspace.buckets.personal!.items).toHaveLength(1);
    expect(workspace.buckets["personal:archived"]!.items).toHaveLength(0);
  });

  it("换账号后迟到的归档响应不会写入新账号列表", async () => {
    const auth = signIn();
    let finish!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const workspace = useWorkspaceStore();
    const pending = workspace.archiveThread(t1, true);
    signIn("user-b", "token-b");
    finish(jsonResponse({ ...t1, archived: true }));
    expect(await pending).toBe(false);
    expect(auth.user?.id).toBe("user-b");
    expect(workspace.buckets.personal!.items).toHaveLength(0);
    expect(workspace.archiveError).toBe("");
  });

  it("个人空间与项目会话分桶隔离，互不串列表", async () => {
    signIn();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input) => {
        const url = new URL(String(input), "http://localhost");
        const pid = url.searchParams.get("project_id");
        const offset = url.searchParams.get("offset");
        if (!pid) {
          return Promise.resolve(
            jsonResponse(
              offset === "20"
                ? { items: [t2], next_offset: null }
                : { items: [t1], next_offset: 20 },
            ),
          );
        }
        if (pid === p1) {
          return Promise.resolve(
            jsonResponse({ items: [pt1], next_offset: null }),
          );
        }
        return Promise.resolve(jsonResponse({ items: [], next_offset: null }));
      });

    const workspace = useWorkspaceStore();
    await workspace.loadScopeThreads(null, true);
    expect(workspace.buckets[PERSONAL_SCOPE]?.items.map((t) => t.id)).toEqual([
      t1.id,
    ]);
    await workspace.loadScopeThreads(null, false);
    expect(workspace.buckets[PERSONAL_SCOPE]?.items.map((t) => t.id)).toEqual([
      t1.id,
      t2.id,
    ]);

    workspace.toggleScope(p1);
    expect(workspace.activeScope).toBe(p1);
    expect(workspace.expandedScopes[p1]).toBe(true);
    await vi.waitFor(() => {
      expect(workspace.buckets[p1]?.loaded).toBe(true);
    });
    expect(workspace.buckets[p1]?.items.map((t) => t.id)).toEqual([pt1.id]);
    expect(workspace.buckets[PERSONAL_SCOPE]?.items.map((t) => t.id)).toEqual([
      t1.id,
      t2.id,
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("upsertThread 按 project_id 归桶，账号切换立即清空所有桶", () => {
    const auth = signIn("user-a");
    const workspace = useWorkspaceStore();
    workspace.upsertThread(t1);
    workspace.upsertThread(pt1);
    expect(workspace.buckets[PERSONAL_SCOPE]?.items.map((t) => t.id)).toEqual([
      t1.id,
    ]);
    expect(workspace.buckets[p1]?.items.map((t) => t.id)).toEqual([pt1.id]);

    auth.user = {
      id: "user-b",
      username: "user-b",
      created_at: "2026-11-20T00:00:00Z",
    };
    expect(workspace.activeScope).toBeNull();
    expect(workspace.buckets[PERSONAL_SCOPE]?.items).toEqual([]);
    expect(workspace.buckets[p1]).toBeUndefined();
  });
});
