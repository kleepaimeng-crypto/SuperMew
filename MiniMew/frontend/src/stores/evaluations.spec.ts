import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import * as api from "../api";
import { useAuthStore } from "./auth";
import { useEvaluationsStore } from "./evaluations";
const a = "00000000-0000-0000-0000-000000000001",
  b = "00000000-0000-0000-0000-000000000002";
const value = (id = a) => ({
  job_id: id,
  status: "running",
  cancel_requested: false,
  error_code: null,
  cases: [{ case_id: "meal", status: "generating", error_code: null }],
});
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
function session() {
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.user = {
    id: "user-a",
    username: "alice",
    created_at: "2026-11-17T00:00:00Z",
  };
  auth.accessToken = "old-token";
  return auth;
}
beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});
afterEach(() => {
  useEvaluationsStore().reset();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("取消请求保留running，等待持久终态", async () => {
  session();
  const request = vi
    .spyOn(api, "request")
    .mockResolvedValueOnce(value())
    .mockResolvedValueOnce({ ...value(), cancel_requested: true });
  const store = useEvaluationsStore();
  await store.select(a);
  await store.cancel();
  expect(store.selected?.status).toBe("running");
  expect(store.selected?.cancel_requested).toBe(true);
  expect(request).toHaveBeenCalledTimes(2);
});
it("未知创建结果重试保留原body和幂等键", async () => {
  session();
  const request = vi
    .spyOn(api, "request")
    .mockRejectedValueOnce(new Error("lost"))
    .mockResolvedValueOnce({ job_id: a })
    .mockResolvedValueOnce({ items: [], next_offset: null });
  const store = useEvaluationsStore();
  const original = { dataset: "original" };
  expect(await store.create(original)).toBe(null);
  expect(store.pending).not.toBe(null);
  expect(await store.create({ dataset: "changed" })).toBe(a);
  expect(request.mock.calls[0]?.[2]).toEqual(original);
  expect(request.mock.calls[1]?.[2]).toEqual(original);
  expect(request.mock.calls[0]?.[5]).toEqual(request.mock.calls[1]?.[5]);
  expect(store.pending).toBe(null);
});
it("切换任务拒绝旧详情响应", async () => {
  session();
  const first = deferred<unknown>();
  vi.spyOn(api, "request")
    .mockReturnValueOnce(first.promise)
    .mockResolvedValueOnce(value(b));
  const store = useEvaluationsStore();
  const opening = store.select(a);
  await store.select(b);
  first.resolve(value(a));
  await opening;
  expect(store.selected?.job_id).toBe(b);
});
it("切换账号清空数据并拒绝旧创建成功回包", async () => {
  const auth = session();
  const first = deferred<unknown>();
  vi.spyOn(api, "request").mockReturnValueOnce(first.promise);
  const store = useEvaluationsStore();
  const creating = store.create({});
  auth.user = { ...auth.user!, id: "user-b" };
  first.resolve({ job_id: a });
  expect(await creating).toBe(null);
  expect(store.pending).toBe(null);
  expect(store.jobs).toEqual([]);
  expect(store.busy).toBe(false);
});
it("401刷新后以相同键重新提交，未伪造新实验", async () => {
  const auth = session();
  const refresh = vi
    .spyOn(auth, "refreshAccess")
    .mockImplementation(async () => {
      auth.accessToken = "new-token";
      return true;
    });
  const request = vi
    .spyOn(api, "request")
    .mockRejectedValueOnce(new api.ApiError(401))
    .mockResolvedValueOnce({ job_id: a })
    .mockResolvedValueOnce({ items: [], next_offset: null });
  const store = useEvaluationsStore();
  expect(await store.create({})).toBe(a);
  expect(refresh).toHaveBeenCalledWith("old-token");
  expect(request.mock.calls[1]?.[3]).toBe("new-token");
  expect(request.mock.calls[0]?.[5]).toEqual(request.mock.calls[1]?.[5]);
});
it("查询故障不宣布任务结束，轮询后仍可恢复原任务", async () => {
  session();
  vi.spyOn(api, "request")
    .mockRejectedValueOnce(new Error())
    .mockResolvedValueOnce(value());
  const store = useEvaluationsStore();
  await store.select(a);
  expect(store.selected).toBe(null);
  expect(store.error).not.toBe("");
  await vi.advanceTimersByTimeAsync(3000);
  expect(store.selected?.job_id).toBe(a);
  expect(store.selected?.status).toBe("running");
});
it("旧Case回包不能污染新任务", async () => {
  session();
  const waiting = deferred<unknown>();
  vi.spyOn(api, "request")
    .mockResolvedValueOnce(value())
    .mockReturnValueOnce(waiting.promise)
    .mockResolvedValueOnce(value(b));
  const store = useEvaluationsStore();
  await store.select(a);
  const old = store.chooseCase("meal");
  await store.select(b);
  waiting.resolve({
    case_id: "meal",
    question: "old",
    status: "completed",
    answer: null,
    error_code: null,
    judge_error: null,
    judge: null,
    counts: null,
    materials: [],
    requested: false,
    accepted: false,
    resumed: false,
  });
  await old;
  expect(store.caseDetail).toBe(null);
  expect(store.selected?.job_id).toBe(b);
});

it("取消之前发出的旧轮询不能清除已保存的取消请求", async () => {
  session();
  const waiting = deferred<unknown>();
  vi.spyOn(api, "request")
    .mockResolvedValueOnce(value())
    .mockReturnValueOnce(waiting.promise)
    .mockResolvedValueOnce({ ...value(), cancel_requested: true });
  const store = useEvaluationsStore();
  await store.select(a);
  vi.advanceTimersByTime(3000);
  await store.cancel();
  waiting.resolve(value());
  await vi.advanceTimersByTimeAsync(0);
  expect(store.selected?.cancel_requested).toBe(true);
  expect(store.selected?.status).toBe("running");
});
