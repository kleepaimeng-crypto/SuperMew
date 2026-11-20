import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";
import { PERSONAL_SCOPE, useWorkspaceStore } from "./workspace";

const t1 = {
  id: "00000000-0000-0000-0000-000000000001",
  title: "个人会话一",
  created_at: "2026-11-20T00:00:00Z",
};
const t2 = {
  id: "00000000-0000-0000-0000-000000000002",
  title: "个人会话二",
  created_at: "2026-11-20T00:01:00Z",
};
const p1 = "11111111-1111-4111-8111-111111111111";
const pt1 = {
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
