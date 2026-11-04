import { defineStore } from "pinia";
import { ref } from "vue";
import {
  ApiError,
  errorMessage,
  parseToken,
  parseUser,
  request,
  type Credentials,
  type User,
} from "../api";

type Status = "idle" | "restoring" | "authenticated" | "anonymous" | "error";

// 仅保存退出意图，不保存任何凭据；刷新页面后优先重试尚未完成的退出。
function pendingLogout(value?: boolean): boolean {
  if (typeof sessionStorage === "undefined") return false;
  if (value === true) sessionStorage.setItem("minimew.logout-pending", "1");
  if (value === false) sessionStorage.removeItem("minimew.logout-pending");
  return sessionStorage.getItem("minimew.logout-pending") === "1";
}

// 同源标签页串行操作 Cookie，避免两个标签同时使用同一 Refresh Token。
async function cookieLock<T>(action: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request("minimew-auth-cookie", action);
  }
  return action(); // 无 Web Locks 时只保证单标签页；开发使用现代浏览器。
}

export const useAuthStore = defineStore("auth", () => {
  const status = ref<Status>("idle");
  const user = ref<User | null>(null);
  const accessToken = ref<string | null>(null);
  const error = ref("");
  const busy = ref(false);
  const logoutPending = ref(pendingLogout());
  let restorePromise: Promise<void> | null = null;
  let mutationPromise: Promise<void> | null = null;
  let generation = 0;

  function clear() {
    user.value = null;
    accessToken.value = null;
  }

  async function accept(value: unknown, epoch: number) {
    const token = parseToken(value);
    const profile = parseUser(
      await request("/auth/me", "GET", undefined, token.access_token),
    );
    if (epoch !== generation || logoutPending.value) return;
    accessToken.value = token.access_token;
    user.value = profile;
    status.value = "authenticated";
  }

  function restore(): Promise<void> {
    if (logoutPending.value) return mutationPromise ?? Promise.resolve();
    if (restorePromise) return restorePromise;
    if (mutationPromise) return mutationPromise;
    const epoch = generation;
    clear();
    status.value = "restoring";
    error.value = "";
    restorePromise = cookieLock(async () => {
      if (epoch !== generation) return;
      try {
        await accept(await request("/auth/refresh", "POST"), epoch);
      } catch (cause) {
        if (epoch !== generation) return;
        clear();
        status.value =
          cause instanceof ApiError && cause.status === 401
            ? "anonymous"
            : "error";
        error.value = status.value === "error" ? errorMessage(cause) : "";
      }
    }).finally(() => {
      restorePromise = null;
    });
    return restorePromise;
  }

  async function ensureReady() {
    if (status.value === "idle" && logoutPending.value) {
      await logout().catch(() => undefined);
      return;
    }
    if (status.value === "idle" || status.value === "restoring")
      await restore();
  }

  function login(credentials: Credentials): Promise<void> {
    if (mutationPromise) return mutationPromise;
    const epoch = ++generation;
    busy.value = true;
    error.value = "";
    clear();
    const pending = restorePromise;
    const job = (async () => {
      await pending;
      await cookieLock(async () => {
        try {
          await accept(
            await request("/auth/login", "POST", credentials),
            epoch,
          );
        } catch (cause) {
          if (epoch !== generation) return;
          status.value =
            cause instanceof ApiError && cause.status === 401
              ? "anonymous"
              : "error";
          error.value = errorMessage(cause);
          throw cause;
        }
      });
    })().finally(() => {
      if (mutationPromise === job) {
        busy.value = false;
        mutationPromise = null;
      }
    });
    mutationPromise = job;
    return job;
  }

  async function register(credentials: Credentials) {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    try {
      parseUser(await request("/auth/register", "POST", credentials));
    } catch (cause) {
      error.value = errorMessage(cause);
      throw cause;
    } finally {
      busy.value = false;
    }
  }

  function logout(): Promise<void> {
    if (logoutPending.value && mutationPromise) return mutationPromise;
    const pending = mutationPromise ?? restorePromise;
    ++generation;
    logoutPending.value = true;
    pendingLogout(true);
    busy.value = true;
    clear();
    error.value = "";
    mutationPromise = (async () => {
      await pending?.catch(() => undefined);
      await cookieLock(async () => {
        try {
          await request("/auth/logout", "POST");
          status.value = "anonymous";
          logoutPending.value = false;
          pendingLogout(false);
        } catch (cause) {
          status.value = "error";
          error.value = "退出尚未完成，请检查网络后重试退出。";
          throw cause;
        }
      });
    })().finally(() => {
      busy.value = false;
      mutationPromise = null;
    });
    return mutationPromise;
  }

  function requireRecovery(token: string) {
    // 已切换凭据的旧请求无权清除当前登录状态。
    if (accessToken.value !== token) return;
    ++generation;
    clear();
    status.value = "error";
    error.value = "登录凭据需要更新，请重试恢复登录。";
  }

  return {
    status,
    user,
    accessToken,
    error,
    busy,
    logoutPending,
    restore,
    ensureReady,
    login,
    register,
    logout,
    requireRecovery,
  };
});
