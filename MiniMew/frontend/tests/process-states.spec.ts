import { test, expect } from "@playwright/test";
import { ids, time, run, messages, started, event } from "./fixtures/chat";
import type { ProcessStepData } from "../src/generated/run-event-v1";
test("失败与成功降级分别解释，历史过程可读", async ({ page }, info) => {
  const tool = { tool_call_id: "search", tool_name: "knowledge_search" };
  const step: ProcessStepData = {
    step_id: "a".repeat(32),
    parent_step_id: "",
    tool_call_id: "search",
    kind: "rerank",
    status: "running",
    query: "餐费标准",
    summary: "",
    count: 0,
    sources: [],
    outcome: "",
    elapsed_ms: 0,
  };
  const events = [
    ...started,
    event("tool.started", 3, tool),
    event("process.step", 4, step),
    event("process.step", 5, {
      ...step,
      status: "completed",
      outcome: "RERANK_UNAVAILABLE",
    }),
    event("process.step", 6, {
      ...step,
      step_id: "b".repeat(32),
      kind: "retrieval",
    }),
    event("process.step", 7, {
      ...step,
      step_id: "b".repeat(32),
      kind: "retrieval",
      status: "failed",
      outcome: "KNOWLEDGE_RETRIEVAL_FAILED",
    }),
    event("tool.failed", 8, {
      ...tool,
      error_code: "KNOWLEDGE_RETRIEVAL_FAILED",
    }),
    event("message.completed", 9, {
      message_id: ids.assistant,
      status: "failed",
      content: "",
    }),
    event("run.failed", 10, {
      status: "failed",
      error_code: "KNOWLEDGE_RETRIEVAL_FAILED",
    }),
  ];
  const thread = { id: ids.thread, title: "过程状态验收", created_at: time };
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().includes("/me")
        ? { id: "u", username: "alice", created_at: time }
        : { access_token: "fixture", token_type: "bearer", expires_in: 900 },
    }),
  );
  await page.route("**/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/messages"))
      return route.fulfill({
        json: {
          items: messages.map((m) =>
            m.role === "assistant"
              ? { ...m, status: "failed", content: "" }
              : m,
          ),
          previous_cursor: null,
        },
      });
    if (path.endsWith("/events"))
      return route.fulfill({ json: { items: events, next_cursor: 10 } });
    if (path.endsWith("/runs/" + ids.run))
      return route.fulfill({
        json: {
          ...run,
          status: "failed",
          error_code: "KNOWLEDGE_RETRIEVAL_FAILED",
          finished_at: time,
        },
      });
    if (path.endsWith("/" + ids.thread)) return route.fulfill({ json: thread });
    return route.fulfill({ json: { items: [thread], next_offset: null } });
  });
  await page.goto("/threads/" + ids.thread);
  await page.locator(".tool-activity summary").click();
  await expect(page.locator(".process-steps")).toContainText("本阶段未完成");
  await expect(page.locator(".process-steps")).toContainText(
    "重排未完成，保留召回顺序",
  );
  await expect(page.locator(".process-steps")).not.toContainText(
    "阶段采用降级结果",
  );
  await page
    .getByText("本阶段未完成", { exact: false })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("failed-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 850 });
  await page
    .getByText("本阶段未完成", { exact: false })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("failed-mobile.png"),
    fullPage: true,
  });
});
