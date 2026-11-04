import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";

const profile = {
  id: "user-a",
  username: "alice",
  created_at: "2026-10-29T00:00:00Z",
};
const token = {
  access_token: "test-only-token",
  token_type: "bearer",
  expires_in: 900,
};
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => {
  setActivePinia(createPinia());
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("身份恢复", () => {
  it("同一页面的并发恢复共用一次刷新，随后才请求用户", async () => {
    const pending = deferred<Response>();
    const fetch = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(json(profile));
    vi.stubGlobal("fetch", fetch);
    const auth = useAuthStore();
    const first = auth.restore();
    const second = auth.restore();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(auth.user).toBeNull();
    pending.resolve(json(token));
    await Promise.all([first, second]);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[1]![0]).toBe("/auth/me");
    expect(fetch.mock.calls[1]![1].headers.get("Authorization")).toBe(
      "Bearer test-only-token",
    );
    expect(auth.status).toBe("authenticated");
    expect(auth.user?.username).toBe("alice");
  });

  it.each([500, 503])("%i 是可重试故障，不是未登录", async (code) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({}, code)));
    const auth = useAuthStore();
    await auth.restore();
    expect(auth.status).toBe("error");
    expect(auth.accessToken).toBeNull();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json(token))
        .mockResolvedValueOnce(json(profile)),
    );
    await auth.restore();
    expect(auth.status).toBe("authenticated");
  });

  it("401 才进入未登录状态", async () => {
    const fetch = vi.fn().mockResolvedValue(json({}, 401));
    vi.stubGlobal("fetch", fetch);
    const auth = useAuthStore();
    await auth.restore();
    expect(auth.status).toBe("anonymous");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("网络错误和畸形响应不当作登录成功", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(json({ access_token: "invalid" }));
    vi.stubGlobal("fetch", fetch);
    const auth = useAuthStore();
    await auth.restore();
    expect(auth.status).toBe("error");
    await auth.restore();
    expect(auth.status).toBe("error");
    expect(auth.accessToken).toBeNull();
  });

  it("退出等待正在执行的刷新，旧响应不能恢复用户", async () => {
    const pending = deferred<Response>();
    const fetch = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(json(profile))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const auth = useAuthStore();
    const restoring = auth.restore();
    const leaving = auth.logout();
    expect(fetch).toHaveBeenCalledTimes(1);
    pending.resolve(json(token));
    await Promise.all([restoring, leaving]);
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "/auth/refresh",
      "/auth/me",
      "/auth/logout",
    ]);
    expect(auth.status).toBe("anonymous");
    expect(auth.user).toBeNull();
    expect(auth.accessToken).toBeNull();
  });

  it("退出故障不假报成功，重试期间不重新刷新身份", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({}, 500))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const auth = useAuthStore();
    await expect(auth.logout()).rejects.toThrow();
    expect(auth.logoutPending).toBe(true);
    await auth.restore();
    expect(fetch).toHaveBeenCalledTimes(1);
    await auth.logout();
    expect(auth.status).toBe("anonymous");
    expect(auth.logoutPending).toBe(false);
  });

  it("注册冲突保留可操作错误，注册不会自动登录", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({}, 409))
        .mockResolvedValueOnce(json(profile, 201)),
    );
    const auth = useAuthStore();
    await expect(
      auth.register({ username: "alice", password: "password-test" }),
    ).rejects.toThrow();
    expect(auth.error).toContain("用户名已被使用");
    await auth.register({ username: "alice", password: "password-test" });
    expect(auth.user).toBeNull();
  });
});
