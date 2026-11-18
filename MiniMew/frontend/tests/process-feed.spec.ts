import { test, expect } from "@playwright/test";
import {
  ids,
  time,
  run,
  messages,
  started,
  event,
  frame,
} from "./fixtures/chat";
import type { ProcessStepData } from "../src/generated/run-event-v1";

test("逐行保留过程、成功后收起、历史重开与刷新", async ({ page }, info) => {
  const gates = [Promise.resolve(), Promise.resolve(), Promise.resolve()];
  const release: (() => void)[] = [];
  gates[1] = new Promise<void>((r) => {
    release[1] = r;
  });
  gates[2] = new Promise<void>((r) => {
    release[2] = r;
  });
  let phase = 0;
  const tool = { tool_call_id: "knowledge", tool_name: "knowledge_search" };
  const query = "国内出差餐费报销上限及所需凭证";
  const step: ProcessStepData = {
    step_id: "a".repeat(32),
    parent_step_id: "",
    tool_call_id: "knowledge",
    kind: "retrieval",
    status: "running",
    query,
    summary: "",
    count: 0,
    sources: [],
    outcome: "",
    elapsed_ms: 0,
  };
  const dense = {
    ...step,
    step_id: "b".repeat(32),
    parent_step_id: step.step_id,
    kind: "dense" as const,
    query: "",
  };
  const model = {
    ...step,
    step_id: "c".repeat(32),
    tool_call_id: "",
    kind: "model" as const,
    query: "",
  };
  const source = {
    document_id: ids.user,
    version_id: ids.assistant,
    title: "差旅报销管理制度 / 国内出差餐费标准与凭证要求",
  };
  const batches = [
    [
      ...started,
      event("tool.started", 3, tool),
      event("process.step", 4, step),
      event("process.step", 5, dense),
    ],
    [
      event("process.step", 6, {
        ...dense,
        status: "completed",
        summary: "向量召回完成",
        count: 3,
        sources: [source],
      }),
      event("process.step", 7, {
        ...step,
        status: "completed",
        summary: "已保留检索材料",
        count: 3,
        sources: [source],
      }),
      event("tool.completed", 8, tool),
      event("process.step", 9, model),
      event("message.delta", 10, {
        message_id: ids.assistant,
        content: "最终答案正在准备",
      }),
    ],
    [
      event("process.step", 11, {
        ...model,
        status: "completed",
        summary: "模型请求结束",
      }),
      event("message.completed", 12, {
        message_id: ids.assistant,
        content: "餐费上限150元，报销需保留凭证。",
        status: "completed",
      }),
      event("run.completed", 13, { status: "succeeded" }),
    ],
  ];
  const all = batches.flat();
  const thread = { id: ids.thread, title: query, created_at: time };
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().includes("/me")
        ? { id: "u", username: "alice", created_at: time }
        : { access_token: "fixture", token_type: "bearer", expires_in: 900 },
    }),
  );
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    if (path.endsWith("/events/stream")) {
      const after = Number(url.searchParams.get("after"));
      const index = after < 5 ? 0 : after < 10 ? 1 : 2;
      await gates[index];
      phase = Math.max(phase, index);
      return route.fulfill({
        contentType: "text/event-stream",
        body: batches[index]!.filter((e) => e.sequence > after)
          .map(frame)
          .join(""),
      });
    }
    if (path.endsWith("/events"))
      return route.fulfill({ json: { items: all, next_cursor: 13 } });
    if (path.endsWith("/messages"))
      return route.fulfill({
        json: {
          items:
            phase === 2
              ? messages.map((m) =>
                  m.role === "assistant"
                    ? {
                        ...m,
                        status: "completed",
                        content: "餐费上限150元，报销需保留凭证。",
                      }
                    : m,
                )
              : messages,
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
        json:
          phase === 2
            ? { ...run, status: "succeeded", finished_at: time }
            : run,
      });
    if (path.endsWith("/" + ids.thread)) return route.fulfill({ json: thread });
    return route.fulfill({ json: { items: [thread], next_offset: null } });
  });
  await page.goto("/threads/" + ids.thread);
  const details = page.locator(".tool-activity"),
    rows = page.locator(".process-steps > li");
  await expect(details).toHaveAttribute("open", "");
  await expect(rows).toHaveCount(2);
  await expect(details).toContainText(query);
  await expect(details).toContainText("正在匹配语义相关片段");
  release[1]!();
  await expect(rows).toHaveCount(3);
  await expect(details).toContainText("向量召回完成");
  await expect(details).toContainText(source.title);
  await expect(page.locator(".answer-body")).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("running-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 850 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("running-mobile.png"),
    fullPage: true,
  });
  release[2]!();
  await expect(page.locator(".answer-body")).toContainText("餐费上限150元");
  await expect(details).not.toHaveAttribute("open", "");
  await page.screenshot({
    path: info.outputPath("completed-collapsed-mobile.png"),
    fullPage: true,
  });
  await details.locator("summary").click();
  await expect(rows).toHaveCount(3);
  await expect(details).toContainText(source.title);
  await page.reload();
  await expect(page.locator(".answer-body")).toContainText("餐费上限150元");
  await expect(details).not.toHaveAttribute("open", "");
  await details.locator("summary").click();
  await expect(rows).toHaveCount(3);
  await expect(details).toContainText(query);
  await page.screenshot({
    path: info.outputPath("completed-mobile.png"),
    fullPage: true,
  });
});
