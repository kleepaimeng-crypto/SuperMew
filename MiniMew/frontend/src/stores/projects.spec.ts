import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import * as api from "../api";
import { useAuthStore } from "./auth";
import { useProjectsStore } from "./projects";
import { useThreadsStore } from "./threads";
import { useDocumentsStore } from "./documents";
const p = "00000000-0000-0000-0000-000000000001",
  q = "00000000-0000-0000-0000-000000000002";
const project = (id = p) => ({
  id,
  name: "项目",
  created_at: "2026-11-18T00:00:00Z",
});
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
function session() {
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.user = {
    id: "user-a",
    username: "alice",
    created_at: "2026-11-18T00:00:00Z",
  };
  auth.accessToken = "test-token";
  return auth;
}
beforeEach(() => {
  setActivePinia(createPinia());
  session();
});
afterEach(() => {
  useProjectsStore().reset();
  useThreadsStore().reset();
  useDocumentsStore().reset();
  vi.restoreAllMocks();
});
it("项目切换拒绝上一项目的迟到记忆", async () => {
  const waiting = deferred<unknown>();
  const call = vi
    .spyOn(api, "request")
    .mockResolvedValueOnce(project())
    .mockReturnValueOnce(waiting.promise)
    .mockResolvedValueOnce(project(q))
    .mockResolvedValueOnce({ items: [] });
  const store = useProjectsStore();
  const first = store.select(p);
  await vi.waitFor(() => expect(call).toHaveBeenCalledTimes(2));
  await store.select(q);
  waiting.resolve({
    items: [
      { id: q, project_id: p, content: "旧项目", enabled: true, revision: 1 },
    ],
  });
  await first;
  expect(store.selected?.id).toBe(q);
  expect(store.memories).toEqual([]);
});
it("换账号清空项目、记忆并拒绝旧请求", async () => {
  const auth = useAuthStore();
  const waiting = deferred<unknown>();
  vi.spyOn(api, "request")
    .mockReturnValueOnce(waiting.promise)
    .mockResolvedValue({ items: [] });
  const store = useProjectsStore();
  const first = store.select(p);
  auth.user = { ...auth.user!, id: "user-b" };
  waiting.resolve(project());
  await first;
  expect(store.selected).toBe(null);
  expect(store.memories).toEqual([]);
  expect(store.items).toEqual([]);
});
it("项目对话发送项目选择且列表限定项目，普通模式恢复无项目参数", async () => {
  const call = vi
    .spyOn(api, "request")
    .mockResolvedValue({ items: [], next_offset: null });
  const store = useThreadsStore();
  store.setProject(p);
  await store.loadList(true);
  expect(call.mock.calls[0]?.[0]).toContain("project_id=" + p);
  store.setProject(null);
  await store.loadList(true);
  expect(call.mock.calls[1]?.[0]).not.toContain("project_id");
});
it("切换文档项目清除未知上传意图，表单项目不是普通文档", async () => {
  const call = vi.spyOn(api, "request").mockRejectedValue(new Error("lost"));
  const store = useDocumentsStore();
  store.setScope(p);
  await store.upload(new File(["hi"], "a.md"));
  const form = call.mock.calls[0]?.[2] as FormData;
  expect(form.get("project_id")).toBe(p);
  expect(store.pendingUpload).not.toBe(null);
  store.setScope(q);
  expect(store.pendingUpload).toBe(null);
  expect(store.selected).toBe(null);
  expect(store.items).toEqual([]);
});
it("记忆冲突显示错误，不自动覆盖服务端新版本", async () => {
  vi.spyOn(api, "request")
    .mockResolvedValueOnce(project())
    .mockResolvedValueOnce({
      items: [
        { id: q, project_id: p, content: "原条目", enabled: true, revision: 1 },
      ],
    })
    .mockRejectedValueOnce(new api.ApiError(409));
  const store = useProjectsStore();
  await store.select(p);
  expect(await store.save("新内容", store.memories[0])).toBe(false);
  expect(store.memories[0]?.revision).toBe(1);
  expect(store.error).toContain("版本已变更");
});
