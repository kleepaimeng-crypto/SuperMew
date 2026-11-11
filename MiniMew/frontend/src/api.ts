import type { components } from "./generated/api";

export type User = components["schemas"]["UserResponse"];
export type Credentials = components["schemas"]["LoginRequest"];
type Token = components["schemas"]["TokenResponse"];

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super("Request failed");
  }
}

// 不自动重试写请求，避免注册、登录或刷新被重复提交。
export async function request(
  path: string,
  method = "GET",
  body?: unknown,
  token?: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const headers = new Headers();
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart)
    headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, {
    method,
    headers,
    body: multipart
      ? body
      : body === undefined
        ? undefined
        : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(15000)])
      : AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.status === 204 ? undefined : response.json();
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// TS 类型不验证网络输入；运行时仍检查消费的字段，拒绝畸形成功响应。
export function parseUser(value: unknown): User {
  if (
    !record(value) ||
    typeof value.id !== "string" ||
    typeof value.username !== "string" ||
    typeof value.created_at !== "string" ||
    !value.id ||
    !value.username ||
    Number.isNaN(Date.parse(value.created_at))
  )
    throw new Error("Invalid user response");
  return {
    id: value.id,
    username: value.username,
    created_at: value.created_at,
  };
}

export function parseToken(value: unknown): Token {
  if (
    !record(value) ||
    typeof value.access_token !== "string" ||
    !value.access_token ||
    value.token_type !== "bearer" ||
    typeof value.expires_in !== "number" ||
    !Number.isInteger(value.expires_in) ||
    value.expires_in <= 0
  )
    throw new Error("Invalid token response");
  return {
    access_token: value.access_token,
    token_type: value.token_type,
    expires_in: value.expires_in,
  };
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "用户名或密码不正确，请重试。";
    if (error.status === 409) return "这个用户名已被使用，请换一个。";
    if (error.status === 422) return "请检查用户名和密码的格式。";
    if (error.status === 403) return "请求未被允许，请从本站页面重新尝试。";
    if (error.status === 429) return "操作较频繁，请稍后重试。";
  }
  return "服务暂时不可用或网络异常，请稍后重试。";
}
