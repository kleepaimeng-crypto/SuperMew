import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { fixture, id, time } from "../document-fixtures";
import { useAuthStore } from "./auth";
import { useDocumentsStore } from "./documents";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
let a = fixture(),
  b = fixture(5);
beforeEach(() => {
  setActivePinia(createPinia());
  a = fixture();
  b = fixture(5);
  const auth = useAuthStore();
  auth.user = { id: id(90), username: "alice", created_at: time };
  auth.accessToken = "token-a";
  auth.status = "authenticated";
});
afterEach(() => {
  useDocumentsStore().reset();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function backend() {
  const fetch = vi.fn(async (path: string) => {
    if (path.includes("?"))
      return json({ items: [a.document], next_offset: null });
    const value = path.includes(b.document.id) ? b : a;
    if (path.includes("/versions/")) return json(value.version);
    if (path.includes("/index-jobs/")) return json(a.job);
    return json(value.document);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
it("上传等待服务端确认，重复点击只发一次，multipart不手写边界", async () => {
  const waiting = deferred<Response>();
  const fetch = backend();
  fetch.mockImplementationOnce(() => waiting.promise);
  const store = useDocumentsStore(),
    file = new File(["hello"], "a.md");
  const pending = store.upload(file);
  expect(store.items).toEqual([]);
  expect(await store.upload(file)).toBeNull();
  const options = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1];
  expect(options.body).toBeInstanceOf(FormData);
  expect((options.headers as Headers).has("Content-Type")).toBe(false);
  waiting.resolve(json(a));
  expect(await pending).toBe(a.document.id);
  expect(
    fetch.mock.calls.filter(([path]) => path === "/v1/documents"),
  ).toHaveLength(1);
});
it("上传回包丢失保留同一意图，手动重试复用原幂等键", async () => {
  const fetch = backend(),
    keys: string[] = [];
  fetch.mockImplementation(async (path, ...args: unknown[]) => {
    if (path === "/v1/documents") {
      keys.push(
        String(
          ((args[0] as RequestInit).body as FormData).get("idempotency_key"),
        ),
      );
      if (keys.length === 1) throw new TypeError("response lost");
      return json(a);
    }
    return json({ items: [a.document], next_offset: null });
  });
  const store = useDocumentsStore();
  expect(await store.upload(new File(["hello"], "a.md"))).toBeNull();
  expect(store.uploadError).toContain("尚未确认");
  expect(await store.retryUpload()).toBe(a.document.id);
  expect(keys[0]).toBe(keys[1]);
  expect(keys).toHaveLength(2);
});
it("非法文件不发送请求", async () => {
  const fetch = backend();
  expect(await useDocumentsStore().upload(new File(["x"], "a.pdf"))).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("较早列表响应不能让已更新的选中文档退回旧进度", async () => {
  const old = fixture().document,
    waiting = deferred<Response>(),
    fetch = backend();
  fetch.mockImplementationOnce(() => waiting.promise);
  const store = useDocumentsStore(),
    loading = store.loadList(true);
  a.job = { ...a.job, status: "running", stage: "embedding", attempt: 1 };
  a.document = { ...a.document, latest_job: a.job };
  await store.selectDocument(a.document.id);
  waiting.resolve(json({ items: [old], next_offset: null }));
  await loading;
  expect(store.items[0]?.latest_job?.status).toBe("running");
  expect(store.selected?.latest_job?.stage).toBe("embedding");
});
it("快速切换文档，迟到A响应不能覆盖B", async () => {
  const fetch = backend(),
    waiting = deferred<Response>();
  fetch.mockImplementationOnce(() => waiting.promise);
  const store = useDocumentsStore(),
    first = store.selectDocument(a.document.id);
  await store.selectDocument(b.document.id);
  waiting.resolve(json(a.document));
  await first;
  expect(store.selected?.id).toBe(b.document.id);
  expect(store.latestVersion?.id).toBe(b.version.id);
});
it("退出/换账号会清空文件和列表，迟到请求不能回填", async () => {
  const waiting = deferred<Response>();
  const fetch = backend();
  fetch.mockImplementationOnce(() => waiting.promise);
  const store = useDocumentsStore(),
    loading = store.loadList(true);
  useAuthStore().user = { id: id(91), username: "bob", created_at: time };
  waiting.resolve(json({ items: [a.document], next_offset: null }));
  await loading;
  expect(store.items).toEqual([]);
  expect(store.pendingUpload).toBeNull();
});
it("活跃Job按原ID补查后读取指针，发布成功即停止轮询", async () => {
  vi.useFakeTimers();
  const fetch = backend(),
    store = useDocumentsStore();
  store.activate();
  await store.loadList(true);
  await store.selectDocument(a.document.id);
  a.job = {
    ...a.job,
    status: "succeeded",
    finished_at: time,
    progress_done: 1,
    progress_total: 1,
  };
  a.document = {
    ...a.document,
    pending_version_id: null,
    current_version_id: a.version.id,
    latest_job: a.job,
  };
  a.version = { ...a.version, status: "published" };
  await vi.advanceTimersByTimeAsync(2000);
  expect(
    fetch.mock.calls.some(([path]) => path === `/v1/index-jobs/${a.job.id}`),
  ).toBe(true);
  expect(store.currentVersion?.id).toBe(a.version.id);
  expect(store.selected?.latest_job?.status).toBe("succeeded");
  expect(vi.getTimerCount()).toBe(0);
});
it("进度网络错误保持最后状态，暂停自动重查并允许重试", async () => {
  vi.useFakeTimers();
  const fetch = backend(),
    store = useDocumentsStore();
  store.activate();
  await store.loadList(true);
  await store.selectDocument(a.document.id);
  fetch.mockRejectedValueOnce(new TypeError("offline"));
  await vi.advanceTimersByTimeAsync(2000);
  expect(store.selected?.latest_job?.status).toBe("pending");
  expect(store.progressError).toContain("上次结果");
  expect(vi.getTimerCount()).toBe(0);
  await store.refresh();
  expect(store.progressError).toBe("");
});
it("离页reset停止轮询并丢弃迟到进度", async () => {
  vi.useFakeTimers();
  const fetch = backend(),
    store = useDocumentsStore();
  store.activate();
  await store.loadList(true);
  await store.selectDocument(a.document.id);
  const waiting = deferred<Response>();
  fetch.mockImplementationOnce(() => waiting.promise);
  const polling = store.poll();
  store.reset();
  waiting.resolve(json(a.job));
  await polling;
  expect(store.items).toEqual([]);
  expect(store.selected).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});
it("401复用统一身份续期，再读取同一文档", async () => {
  const fetch = backend();
  fetch.mockResolvedValueOnce(json({}, 401));
  const auth = useAuthStore();
  const renew = vi.spyOn(auth, "refreshAccess").mockImplementation(async () => {
    auth.accessToken = "token-new";
    return true;
  });
  const store = useDocumentsStore();
  await store.selectDocument(a.document.id);
  expect(renew).toHaveBeenCalledOnce();
  expect(store.selected?.id).toBe(a.document.id);
  const options = (fetch.mock.calls[1] as unknown as [string, RequestInit])[1];
  expect((options.headers as Headers).get("Authorization")).toBe(
    "Bearer token-new",
  );
});
