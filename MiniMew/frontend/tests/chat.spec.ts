import { test, expect, type Page } from "@playwright/test";
import {
  body,
  cancelled,
  frame,
  ids,
  messages,
  run,
  started,
  success,
  time,
} from "./fixtures/chat";

async function fixture(
  page: Page,
  mode:
    | "normal"
    | "submit"
    | "cancel"
    | "refresh"
    | "failure"
    | "history-error" = "normal",
) {
  let created = mode !== "submit" && mode !== "history-error";
  let complete = false;
  let cancelRequested = false;
  let refreshes = 0;
  let streams = 0;
  const posts: { idempotency_key: string; content: string }[] = [];
  const cursors: string[] = [];
  const headers: string[] = [];
  let historyError = mode === "history-error";
  const thread = { id: ids.thread, title: "第一段对话", created_at: time };
  const other = {
    id: "00000000-0000-0000-0000-000000000099",
    title: "另一段对话",
    created_at: time,
  };
  await page.route("**/auth/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/refresh")) {
      ++refreshes;
      return route.fulfill({
        json: {
          access_token: `token-${refreshes}`,
          token_type: "bearer",
          expires_in: 900,
        },
      });
    }
    if (path.endsWith("/me"))
      return route.fulfill({
        json: { id: "user-a", username: "alice", created_at: time },
      });
    return route.fulfill({ status: 204 });
  });
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/messages")) {
      if (created && historyError)
        return route.fulfill({ status: 503, json: {} });
      const content =
        mode === "cancel" || mode === "failure" ? "今天" : "今天天气很好";
      const status =
        mode === "cancel"
          ? "cancelled"
          : mode === "failure"
            ? "failed"
            : "completed";
      return route.fulfill({
        json: {
          items:
            path.includes(other.id) || !created
              ? []
              : complete
                ? messages.map((m) =>
                    m.role === "assistant" ? { ...m, status, content } : m,
                  )
                : messages,
          previous_cursor: null,
        },
      });
    }
    if (path.endsWith("/runs") && route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      created = true;
      if (mode === "submit" && posts.length === 1) return route.abort("failed");
      return route.fulfill({
        status: posts.length === 1 ? 201 : 200,
        json: run,
      });
    }
    if (path.endsWith("/cancel")) {
      cancelRequested = true;
      return route.fulfill({
        status: 202,
        json: { ...run, cancel_requested_at: time },
      });
    }
    if (path.endsWith("/events/stream")) {
      ++streams;
      cursors.push(url.searchParams.get("after")!);
      headers.push(route.request().headers().authorization!);
      if (mode === "refresh" && streams === 2)
        return route.fulfill({ status: 401, json: {} });
      if (mode === "history-error") {
        complete = true;
        return route.fulfill({
          contentType: "text/event-stream",
          body: success.map(frame).join(""),
        });
      }
      if (streams === 1)
        return route.fulfill({
          contentType: "text/event-stream",
          body: [...started, body].map(frame).join(""),
        });
      if (mode === "cancel" && !cancelRequested)
        return route.fulfill({
          contentType: "text/event-stream",
          body: frame(body),
        });
      complete = true;
      const tail =
        mode === "cancel"
          ? cancelled
          : mode === "failure"
            ? [
                {
                  ...success[3]!,
                  data: {
                    message_id: ids.assistant,
                    status: "failed",
                    content: "今天",
                  },
                },
                {
                  ...success[4]!,
                  type: "run.failed",
                  data: { status: "failed", error_code: "PROVIDER_TIMEOUT" },
                },
              ]
            : success.slice(2);
      return route.fulfill({
        contentType: "text/event-stream",
        body: tail.map((item) => frame(item as typeof body)).join(""),
      });
    }
    if (path.endsWith(`/runs/${ids.run}`))
      return route.fulfill({
        json: complete
          ? {
              ...run,
              status:
                mode === "cancel"
                  ? "cancelled"
                  : mode === "failure"
                    ? "failed"
                    : "succeeded",
              finished_at: time,
              cancel_requested_at: cancelRequested ? time : null,
              error_code: mode === "failure" ? "PROVIDER_TIMEOUT" : null,
            }
          : run,
      });
    if (path.endsWith(thread.id)) return route.fulfill({ json: thread });
    if (path.endsWith(other.id)) return route.fulfill({ json: other });
    return route.fulfill({
      json: { items: [thread, other], next_offset: null },
    });
  });
  return {
    posts,
    cursors,
    headers,
    get refreshes() {
      return refreshes;
    },
    recoverHistory() {
      historyError = false;
    },
  };
}

test("历史、流式重放、最终替换、刷新恢复与桌面/移动布局", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/threads/${ids.thread}`);
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  await expect(page.getByText("已完成", { exact: true })).toBeVisible();
  expect(state.cursors.slice(0, 2)).toEqual(["0", "3"]);
  await expect(page.getByRole("article", { name: "MiniMew 回复" })).toHaveCount(
    1,
  );
  await page.screenshot({
    path: "../.artifacts/chat-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("你的问题", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.artifacts/chat-mobile.png",
    fullPage: true,
  });
  await page.reload();
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "MiniMew 回复" })).toHaveCount(
    1,
  );
});
test("未知提交刷新后使用原 key 和正文重试，不创建第二个问题", async ({
  page,
}) => {
  const state = await fixture(page, "submit");
  await page.goto(`/threads/${ids.thread}`);
  await page.getByLabel("你的问题", { exact: true }).fill("保存原问题");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试原提交", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("你的问题", { exact: true })).toHaveValue(
    "保存原问题",
  );
  await page.getByRole("button", { name: "重试原提交", exact: true }).click();
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1]).toEqual(state.posts[0]);
  const saved = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(saved).not.toContain("token-");
  expect(saved).not.toContain("保存原问题");
});
test("提交成功但历史失败可独立恢复，不重新 POST", async ({ page }) => {
  const state = await fixture(page, "history-error");
  await page.goto(`/threads/${ids.thread}`);
  await page.getByLabel("你的问题", { exact: true }).fill("你好");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(page.getByText(/问题已提交，消息记录尚未同步/)).toBeVisible();
  state.recoverHistory();
  await page.getByRole("button", { name: "重试同步历史", exact: true }).click();
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(1);
});
test("停止请求等待权威取消，保留已保存正文", async ({ page }) => {
  await fixture(page, "cancel");
  await page.goto(`/threads/${ids.thread}`);
  await expect(page.getByText("今天", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "停止回复", exact: true }).click();
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
  await expect(page.getByText("今天", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "停止回复", exact: true }),
  ).toHaveCount(0);
});
test("SSE 身份到期自动刷新，从原游标继续", async ({ page }) => {
  const state = await fixture(page, "refresh");
  await page.goto(`/threads/${ids.thread}`);
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  expect(state.cursors.slice(0, 3)).toEqual(["0", "3", "3"]);
  expect(state.headers.slice(0, 3)).toEqual([
    "Bearer token-1",
    "Bearer token-1",
    "Bearer token-2",
  ]);
  expect(state.refreshes).toBe(2);
});
test("失败保留半截正文，切换对话与退出不带出旧消息", async ({ page }) => {
  await fixture(page, "failure");
  await page.goto(`/threads/${ids.thread}`);
  await expect(page.getByText("回答未完成", { exact: true })).toBeVisible();
  await expect(page.getByText("今天", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: /另一段对话/ }).click();
  await expect(page.getByRole("article")).toHaveCount(0);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("article")).toHaveCount(0);
});
