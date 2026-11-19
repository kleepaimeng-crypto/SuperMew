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
import type {
  ProcessStepData,
  ProcessDetailData,
  RunEvent,
} from "../src/generated/run-event-v1";

test("任务主线归组并行补查、缺口与模型用途；详情按需打开，刷新仍可读", async ({
  page,
}, info) => {
  const events: RunEvent[] = [...started];
  function add<T extends RunEvent["type"]>(
    type: T,
    data: Extract<RunEvent, { type: T }>["data"],
  ) {
    const envelope = event("run.started", events.length + 1, {
      status: "running",
    });
    events.push({ ...envelope, type, data } as RunEvent);
  }
  let index = 0;
  function begin(
    kind: ProcessStepData["kind"],
    parent = "",
    query = "",
    call = "search",
  ) {
    const step: ProcessStepData = {
      step_id: (++index).toString(16).padStart(32, "0"),
      parent_step_id: parent,
      tool_call_id: call,
      kind,
      status: "running",
      query,
      summary: "",
      count: 0,
      sources: [],
      elapsed_ms: 0,
      outcome: "",
    };
    add("process.step", step);
    return step;
  }
  function metadata(
    step: ProcessStepData,
    patch: Partial<ProcessDetailData>,
    stage: "start" | "result" = "result",
  ) {
    add("process.detail", {
      step_id: step.step_id,
      stage,
      phase: "",
      purpose: "",
      attempt: 0,
      missing: [],
      questions: [],
      sources: [],
      ...patch,
    });
  }
  function end(step: ProcessStepData, patch: Partial<ProcessStepData> = {}) {
    add("process.step", {
      ...step,
      status: "completed",
      elapsed_ms: 1200,
      ...patch,
    });
  }
  const source = {
    document_id: ids.user,
    version_id: ids.assistant,
    name: "链路仿真正式启动指南.md",
    version_number: 3,
    sections: ["10. 备份网络状态", "14. 执行 dry-run"],
  };
  const query = "链路仿真项目正式启动前需要做哪些准备工作？";
  const selection = begin("model", "", "", "");
  metadata(selection, { purpose: "tool_selection", attempt: 1 });
  end(selection, { outcome: "tool_calls" });
  add("tool.started", {
    tool_call_id: "search",
    tool_name: "knowledge_search",
  });
  function retrieval(
    parent: string,
    text: string,
    phase: ProcessDetailData["phase"],
  ) {
    const s = begin("retrieval", parent, text);
    metadata(s, { phase }, "start");
    for (const kind of [
      "dense",
      "bm25",
      "fusion",
      "merge",
      "rerank",
    ] as const) {
      const internal = begin(kind, s.step_id);
      end(internal, {
        count: kind === "dense" || kind === "bm25" ? 10 : 12,
        elapsed_ms: 30,
      });
    }
    metadata(s, { phase, sources: [source] });
    end(s, { count: 6, outcome: "ok" });
  }
  function assess(
    phase: ProcessDetailData["phase"],
    missing: string[],
    parent = "",
  ) {
    const s = begin("assessment", parent);
    metadata(s, { phase, missing });
    end(s, {
      count: 6,
      outcome: missing.length ? "insufficient" : "sufficient",
    });
  }
  retrieval("", query, "initial");
  assess("initial", [
    "缺少备份文件保存路径",
    "尚未找到权限和服务状态检查步骤",
    "缺少恢复验证方法",
  ]);
  const plan = begin("plan");
  const questions = [
    "需要备份哪些网络状态？",
    "怎样验证网卡与连通性？",
    "需要检查哪些依赖、权限和服务？",
  ];
  metadata(plan, { questions });
  end(plan, { count: 3 });
  const children = questions.map((q) => begin("subquestion", "", q));
  // 完成顺序刻意不同于计划顺序。
  for (const i of [1, 0, 2]) {
    const child = children[i]!;
    retrieval(child.step_id, questions[i]!, "child");
    assess("child", ["缺少具体校验命令"], child.step_id);
    metadata(child, { phase: "child", missing: ["缺少具体校验命令"] });
    end(child, { count: 6, outcome: "insufficient" });
  }
  assess("aggregate", ["缺少完整恢复验证方法"]);
  const rewrite = begin("rewrite");
  metadata(rewrite, { phase: "rewritten" });
  end(rewrite);
  retrieval(
    "",
    "备份 iptables 和路由表后的恢复验证方法与检查命令",
    "rewritten",
  );
  assess("rewritten", ["未找到恢复验证命令"]);
  add("tool.completed", {
    tool_call_id: "search",
    tool_name: "knowledge_search",
  });
  const answer = begin("model", "", "", "");
  metadata(answer, { purpose: "answer", attempt: 1 });
  end(answer);
  const repair = begin("model", "", "", "");
  metadata(repair, { purpose: "answer_repair", attempt: 1 }, "start");
  const gateAt = events.length;
  let finish!: () => void;
  const gate = new Promise<void>((r) => {
    finish = r;
  });
  metadata(repair, { purpose: "answer_repair", attempt: 1 });
  end(repair);
  const content =
    "启动前应先备份网络状态、核对网卡和连通性，再检查依赖与权限。资料尚未提供恢复验证命令。";
  add("message.completed", {
    message_id: ids.assistant,
    content,
    status: "completed",
  });
  add("run.completed", { status: "succeeded" });
  let completed = false;
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
      if (after >= gateAt) {
        await gate;
        completed = true;
      }
      const limit = completed ? events.length : gateAt;
      return route.fulfill({
        contentType: "text/event-stream",
        body: events
          .filter((e) => e.sequence > after && e.sequence <= limit)
          .map(frame)
          .join(""),
      });
    }
    if (path.endsWith("/events")) {
      const items = events
        .filter((e) => e.sequence > Number(url.searchParams.get("after")))
        .slice(0, Number(url.searchParams.get("limit") ?? 100));
      return route.fulfill({
        json: { items, next_cursor: items.at(-1)?.sequence ?? 0 },
      });
    }
    if (path.endsWith("/messages"))
      return route.fulfill({
        json: {
          items: messages.map((m) =>
            m.role === "assistant" && completed
              ? { ...m, status: "completed", content }
              : m,
          ),
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
        json: completed
          ? { ...run, status: "succeeded", finished_at: time }
          : run,
      });
    if (path.endsWith("/" + ids.thread)) return route.fulfill({ json: thread });
    return route.fulfill({ json: { items: [thread], next_offset: null } });
  });
  await page.goto("/threads/" + ids.thread);
  const feed = page.locator(".tool-activity"),
    rows = page.locator(".process-tasks > li");
  await expect(rows).toHaveCount(10);
  await expect(feed).toHaveAttribute("open", "");
  await expect(feed.getByText("首次检索资料", { exact: true })).toBeVisible();
  await expect(feed).toContainText("尚缺：缺少备份文件保存路径");
  await expect(feed).toContainText("补查 3 个方向");
  await expect(feed.locator(".subtasks > .task-row")).toHaveCount(3);
  await expect(feed.locator(".subtasks > .task-row").first()).toContainText(
    questions[0]!,
  );
  await expect(feed).toContainText("校正回答格式");
  await expect(
    feed.getByText("向量召回", { exact: true }).first(),
  ).not.toBeVisible();
  await expect(
    feed.getByText("<0.1秒", { exact: false }).first(),
  ).not.toBeVisible();
  await page.locator(".message-feed").evaluate((e) => {
    e.scrollTop = 0;
  });
  await page.screenshot({
    path: info.outputPath("taskline-desktop.png"),
    fullPage: true,
  });
  const retrievalDetails = rows.nth(1).locator("details");
  await retrievalDetails.locator(":scope > summary").click();
  await expect(
    retrievalDetails.getByText("向量召回", { exact: true }),
  ).toBeVisible();
  await expect(retrievalDetails).toContainText("10个候选片段");
  await expect(retrievalDetails).toContainText("<0.1秒");
  await expect(retrievalDetails).toContainText("版本 3");
  await expect(retrievalDetails).toContainText("10. 备份网络状态");
  await expect(
    retrievalDetails.getByText(source.name, { exact: true }),
  ).toHaveCount(1);
  await page.screenshot({
    path: info.outputPath("details-desktop.png"),
    fullPage: true,
  });
  await retrievalDetails
    .getByText("10. 备份网络状态", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("source-desktop.png"),
    fullPage: true,
  });
  await retrievalDetails.locator(":scope > summary").click();
  await page.setViewportSize({ width: 390, height: 850 });
  await page.locator(".message-feed").evaluate((e) => {
    e.scrollTop = 0;
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("taskline-mobile.png"),
    fullPage: true,
  });
  await feed.locator(".subtasks > .task-row").first().scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("subtasks-mobile.png"),
    fullPage: true,
  });
  finish();
  await expect(page.locator(".answer-body")).toContainText(content);
  await expect(feed).not.toHaveAttribute("open", "");
  await page.reload();
  await expect(page.locator(".answer-body")).toContainText(content);
  await feed.locator(":scope > summary").click();
  await expect(rows).toHaveCount(10);
  await expect(feed).toContainText("未找到恢复验证命令");
  await rows.nth(1).locator("details > summary").click();
  await expect(rows.nth(1)).toContainText(source.version_id);
  await rows
    .nth(1)
    .getByText("10. 备份网络状态", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("replay-details-mobile.png"),
    fullPage: true,
  });
});
