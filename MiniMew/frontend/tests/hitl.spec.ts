import { test, expect } from "@playwright/test";
import {
  event,
  frame,
  ids,
  messages,
  run,
  started,
  time,
} from "./fixtures/chat";

for (const width of [390, 1280]) {
  test(`刷新等待并补充原任务 ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 850 });
    let done = false;
    let newRuns = 0;
    const answers: unknown[] = [];
    const checkpoint = "00000000-0000-0000-0000-000000000010";
    const output = "国内餐费150元。";
    const events = [
      ...started,
      event("tool.started", 3, {
        tool_call_id: "meal",
        tool_name: "knowledge_search",
      }),
      event("hitl.required", 4, {
        checkpoint_id: checkpoint,
        prompt: "国内还是海外？",
        expires_at: "2099-01-01T12:00:00Z",
      }),
    ];
    const completed = [
      event("hitl.accepted", 5, { checkpoint_id: checkpoint }),
      event("run.resumed", 6, { status: "running" }),
      event("tool.completed", 7, {
        tool_call_id: "meal",
        tool_name: "knowledge_search",
      }),
      event("message.completed", 8, {
        message_id: ids.assistant,
        status: "completed",
        content: output,
      }),
      event("run.completed", 9, { status: "succeeded" }),
    ];
    await page.route("**/auth/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({
        json: path.endsWith("/refresh")
          ? {
              access_token: "test-token",
              token_type: "bearer",
              expires_in: 900,
            }
          : { id: "user-a", username: "alice", created_at: time },
      });
    });
    await page.route("**/v1/**", (route) => {
      const url = new URL(route.request().url()),
        path = url.pathname;
      if (path.endsWith("/resume-credential"))
        return route.fulfill({
          json: {
            checkpoint_id: checkpoint,
            prompt: "国内还是海外？",
            expires_at: "2099-01-01T12:00:00Z",
            token: "x".repeat(43),
          },
        });
      if (path.endsWith("/resume")) {
        answers.push(route.request().postDataJSON());
        done = true;
        return route.fulfill({
          status: 202,
          json: { ...run, status: "resume_pending" },
        });
      }
      if (path.endsWith("/messages"))
        return route.fulfill({
          json: {
            items: messages.map((m) =>
              m.role === "assistant"
                ? {
                    ...m,
                    status: done ? "completed" : "waiting_input",
                    content: done ? output : "",
                  }
                : m,
            ),
            previous_cursor: null,
          },
        });
      if (path.endsWith("/events/stream")) {
        const after = Number(url.searchParams.get("after") ?? 0);
        const available = (done ? [...events, ...completed] : events).filter(
          (e) => e.sequence > after,
        );
        return route.fulfill({
          contentType: "text/event-stream",
          body: available.map(frame).join(""),
        });
      }
      if (path.endsWith("/answer"))
        return route.fulfill({
          json: {
            run_id: ids.run,
            message_id: ids.assistant,
            status: "succeeded",
            materials: null,
          },
        });
      if (path.endsWith(`/runs/${ids.run}`))
        return route.fulfill({
          json: {
            ...run,
            status: done ? "succeeded" : "waiting_input",
            finished_at: done ? time : null,
          },
        });
      if (path.endsWith("/runs") && route.request().method() === "POST")
        newRuns++;
      if (path.endsWith("/threads"))
        return route.fulfill({
          json: {
            items: [{ id: ids.thread, title: "差旅制度", created_at: time }],
            next_offset: null,
          },
        });
      return route.fulfill({
        json: { id: ids.thread, title: "差旅制度", created_at: time },
      });
    });
    await page.goto(`/threads/${ids.thread}`);
    await expect(page.getByLabel("请补充：国内还是海外？")).toHaveCount(0);
    await expect(page.getByLabel("国内还是海外？")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("国内还是海外？")).toBeVisible();
    await page.screenshot({
      path: info.outputPath("waiting.png"),
      fullPage: true,
    });
    await page.getByLabel("国内还是海外？").fill("国内");
    await page.getByRole("button", { name: "补充并继续" }).click();
    await expect(page.getByText(output, { exact: true })).toBeVisible();
    await expect(page.getByLabel("国内还是海外？")).toHaveCount(0);
    expect(answers).toHaveLength(1);
    expect(answers[0]).toMatchObject({
      checkpoint_id: checkpoint,
      answer: "国内",
    });
    expect(newRuns).toBe(0);
    await expect(page.getByLabel("你的消息", { exact: true })).toHaveCount(1);
    await expect(page.getByLabel("MiniMew 回复", { exact: true })).toHaveCount(
      1,
    );
  });
}
