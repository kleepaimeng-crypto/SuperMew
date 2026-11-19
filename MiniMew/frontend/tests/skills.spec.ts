import { test, expect } from "@playwright/test";
import {
  ids,
  time,
  run,
  messages,
  event,
  started,
  frame,
} from "./fixtures/chat";

test("手动模式的未知提交与刷新仍重试原版本，历史显示冻结版本", async ({
  page,
}, info) => {
  const skill = {
    id: "document-qa",
    version: "1.0.0",
    name: "文档问答",
    description: "依据资料整理回答",
    digest: "a".repeat(64),
  };
  let posted: Record<string, unknown>[] = [];
  let accepted = false,
    done = false;
  const tool = { tool_call_id: "resource", tool_name: "read_skill_resource" };
  const content = "已按文档问答参考规范整理回答。";
  const events = [
    ...started,
    event("tool.started", 3, tool),
    event("tool.completed", 4, tool),
    event("message.completed", 5, {
      message_id: ids.assistant,
      status: "completed",
      content,
    }),
    event("run.completed", 6, { status: "succeeded" }),
  ];
  const thread = { id: ids.thread, title: "整理资料", created_at: time };
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().includes("/me")
        ? { id: "user-a", username: "alice", created_at: time }
        : { access_token: "fixture", token_type: "bearer", expires_in: 900 },
    }),
  );
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/v1/skills")
      return route.fulfill({
        json: [{ ...skill, version: posted.length ? "2.0.0" : "1.0.0" }],
      });
    if (path.endsWith("/runs") && route.request().method() === "POST") {
      posted.push(route.request().postDataJSON());
      if (posted.length === 1) return route.fulfill({ status: 503, json: {} });
      accepted = true;
      return route.fulfill({ status: 200, json: { ...run, skill } });
    }
    if (path.endsWith("/events/stream")) {
      done = true;
      return route.fulfill({
        contentType: "text/event-stream",
        body: events.map(frame).join(""),
      });
    }
    if (path.endsWith("/events"))
      return route.fulfill({ json: { items: events, next_cursor: 6 } });
    if (path.endsWith("/messages"))
      return route.fulfill({
        json: {
          items: accepted
            ? messages.map((m) =>
                m.role === "assistant" && done
                  ? { ...m, status: "completed", content }
                  : m,
              )
            : [],
          previous_cursor: null,
        },
      });
    if (path.endsWith("/answer"))
      return route.fulfill({
        json: {
          run_id: ids.run,
          message_id: ids.assistant,
          status: "succeeded",
          materials: null,
        },
      });
    if (path.endsWith("/runs/" + ids.run))
      return route.fulfill({
        json: {
          ...run,
          skill,
          ...(done ? { status: "succeeded", finished_at: time } : {}),
        },
      });
    if (path.endsWith("/" + ids.thread)) return route.fulfill({ json: thread });
    return route.fulfill({ json: { items: [thread], next_offset: null } });
  });
  await page.goto("/threads/" + ids.thread);
  await page
    .getByLabel("任务模式", { exact: true })
    .selectOption("document-qa@1.0.0");
  await page.getByLabel("你的问题", { exact: true }).fill("整理资料");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(page.getByText(/提交结果未确认/)).toBeVisible();
  await expect(page.getByLabel("任务模式", { exact: true })).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel("任务模式", { exact: true })).toHaveValue(
    "document-qa@1.0.0",
  );
  await expect(page.getByLabel("任务模式", { exact: true })).toBeDisabled();
  await expect(page.locator("#chat-skill option:checked")).toHaveText(
    "文档问答 · v1.0.0（待确认）",
  );
  await page.screenshot({
    path: info.outputPath("pending-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "重试原提交", exact: true }).click();
  await expect(page.locator(".answer-body")).toContainText(content);
  await expect(page.getByLabel("任务模式", { exact: true })).toHaveValue("");
  expect(posted[1]).toEqual(posted[0]);
  expect(posted[1]?.skill).toEqual({ id: "document-qa", version: "1.0.0" });
  await expect(page.locator(".message-skill")).toContainText(
    "文档问答 · v1.0.0",
  );
  await page.locator(".tool-activity > summary").click();
  await expect(page.locator(".tool-activity")).toContainText(
    "读取任务参考资料",
  );
  await page.screenshot({
    path: info.outputPath("selected-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 850 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("selected-mobile.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(page.locator(".message-skill")).toContainText(
    "文档问答 · v1.0.0",
  );
  await expect(page.getByLabel("任务模式", { exact: true })).toHaveValue("");
  await expect(page.getByRole("option", { name: /v2.0.0/ })).toHaveCount(1);
});

test("模式目录失败明确提示并可重试，默认助手保留", async ({ page }, info) => {
  let failed = true;
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().includes("/me")
        ? { id: "user-a", username: "alice", created_at: time }
        : { access_token: "fixture", token_type: "bearer", expires_in: 900 },
    }),
  );
  const thread = { id: ids.thread, title: "默认模式", created_at: time };
  await page.route("**/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/v1/skills")
      return route.fulfill(failed ? { status: 503, json: {} } : { json: [] });
    if (path.endsWith("/messages"))
      return route.fulfill({ json: { items: [], previous_cursor: null } });
    if (path.endsWith("/" + ids.thread)) return route.fulfill({ json: thread });
    return route.fulfill({ json: { items: [thread], next_offset: null } });
  });
  await page.goto("/threads/" + ids.thread);
  await expect(page.getByText(/任务模式暂时无法加载/)).toBeVisible();
  await expect(page.getByLabel("任务模式", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("任务模式", { exact: true })).toHaveValue("");
  await page.screenshot({
    path: info.outputPath("catalog-error-desktop.png"),
    fullPage: true,
  });
  failed = false;
  await page.getByRole("button", { name: "重试加载模式", exact: true }).click();
  await expect(page.getByText(/任务模式暂时无法加载/)).toHaveCount(0);
});
