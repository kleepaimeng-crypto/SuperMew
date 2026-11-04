import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";
import { useThreadsStore } from "./threads";

const a = {
  id: "00000000-0000-0000-0000-000000000001",
  title: "A 对话",
  created_at: "2026-10-29T00:00:00Z",
};
const b = { ...a, id: "00000000-0000-0000-0000-000000000002", title: "B 对话" };
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => {
  setActivePinia(createPinia());
  const auth = useAuthStore();
  auth.user = { id: "user-a", username: "alice", created_at: a.created_at };
  auth.accessToken = "token-a";
  auth.status = "authenticated";
});
afterEach(() => vi.unstubAllGlobals());

it("快速切换时较晚返回的 A 不覆盖 B", async () => {
  const pending = deferred<Response>();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(json(b)),
  );
  const store = useThreadsStore();
  const first = store.selectThread(a.id);
  await store.selectThread(b.id);
  pending.resolve(json(a));
  await first;
  expect(store.selected).toEqual(b);
  expect(store.detailLoading).toBe(false);
});

it("账号切换清空数据并拒绝旧用户响应", async () => {
  const pending = deferred<Response>();
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));
  const store = useThreadsStore();
  store.items = [a];
  store.selected = a;
  const loading = store.loadList();
  useAuthStore().user = {
    id: "user-b",
    username: "bob",
    created_at: a.created_at,
  };
  expect(store.items).toEqual([]);
  expect(store.selected).toBeNull();
  pending.resolve(json({ items: [a], next_offset: null }));
  await loading;
  expect(store.items).toEqual([]);
});

it("等待创建成功才显示，连续点击不发第二个 POST", async () => {
  const pending = deferred<Response>();
  const fetch = vi
    .fn()
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(json({ items: [a], next_offset: null }));
  vi.stubGlobal("fetch", fetch);
  const store = useThreadsStore();
  const first = store.createThread();
  expect(await store.createThread()).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(store.items).toEqual([]);
  pending.resolve(json(a, 201));
  expect(await first).toBe(a.id);
  expect(store.items[0]).toEqual(a);
  expect(
    fetch.mock.calls.filter((call) => call[1].method === "POST"),
  ).toHaveLength(1);
});

it("创建失败不追加假对话，不自动重试", async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError("network"));
  vi.stubGlobal("fetch", fetch);
  const store = useThreadsStore();
  expect(await store.createThread()).toBeNull();
  expect(store.items).toEqual([]);
  expect(store.createError).toContain("先刷新列表");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("等待创建期间主动切换，不抢回用户选择", async () => {
  const pending = deferred<Response>();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(json(b))
      .mockResolvedValueOnce(json({ items: [a, b], next_offset: null })),
  );
  const store = useThreadsStore();
  const creating = store.createThread();
  await store.selectThread(b.id);
  pending.resolve(json(a, 201));
  expect(await creating).toBeNull();
  expect(store.selected).toEqual(b);
});

it("分页按 ID 去重，重新加载失败后从第一页重试", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(json({ items: [a], next_offset: 20 }))
    .mockResolvedValueOnce(json({ items: [a, b], next_offset: null }))
    .mockResolvedValueOnce(json({}, 500))
    .mockResolvedValueOnce(json({ items: [b], next_offset: null }));
  vi.stubGlobal("fetch", fetch);
  const store = useThreadsStore();
  await store.loadList();
  await store.loadList();
  expect(store.items).toEqual([a, b]);
  expect(fetch.mock.calls[1]![0]).toContain("offset=20");
  await store.loadList(true);
  expect(store.listError).toBeTruthy();
  await store.loadList(!store.loaded);
  expect(fetch.mock.calls[3]![0]).toContain("offset=0");
  expect(store.items).toEqual([b]);
});

it("404 与非法地址显示不可访问，500 可以重试", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(json({}, 404))
    .mockResolvedValueOnce(json({}, 500))
    .mockResolvedValueOnce(json(a));
  vi.stubGlobal("fetch", fetch);
  const store = useThreadsStore();
  await store.selectThread("invalid");
  expect(store.unavailable).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
  await store.selectThread(a.id);
  expect(store.unavailable).toBe(true);
  await store.selectThread(a.id);
  expect(store.detailError).toBeTruthy();
  expect(store.unavailable).toBe(false);
  await store.selectThread(a.id);
  expect(store.selected).toEqual(a);
});

it("401 进入身份恢复状态并清空对话；旧凭据不能清除新登录", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({}, 401)));
  const auth = useAuthStore();
  const store = useThreadsStore();
  store.items = [a];
  await store.selectThread(a.id);
  expect(auth.status).toBe("error");
  expect(store.items).toEqual([]);
  auth.accessToken = "token-b";
  auth.status = "authenticated";
  auth.requireRecovery("token-a");
  expect(auth.status).toBe("authenticated");
});

it("畸形响应不进入列表；详情 ID 必须匹配请求", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(json({ items: [a], next_offset: 0 }))
      .mockResolvedValueOnce(json(b)),
  );
  const store = useThreadsStore();
  await store.loadList();
  expect(store.items).toEqual([]);
  expect(store.listError).toBeTruthy();
  await store.selectThread(a.id);
  expect(store.selected).toBeNull();
  expect(store.detailError).toBeTruthy();
});
