import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const a = "00000000-0000-0000-0000-000000000001",
  b = "00000000-0000-0000-0000-000000000002",
  corpus = "00000000-0000-0000-0000-000000000003";
const time = "2026-11-17T00:00:00Z";
const legacy = JSON.parse(
  readFileSync(
    new URL("../../evals/rag/fixtures/baseline.json", import.meta.url),
    "utf8",
  ),
);
function artifact(decision = "pass") {
  const report = structuredClone(legacy);
  return {
    schema_version: 2,
    scorer_version: "controlled-judge-v2",
    source_stages: "local-retrieval/controlled-generation/controlled-judge",
    structural: report,
    evaluations: [
      {
        case_id: "meal",
        answer: "每日150元",
        judge: {
          correctness: 2,
          completeness: 2,
          grounding: 2,
          reason: "受控",
        },
        judge_error: null,
        requested: true,
        matched: true,
        accepted: true,
        resumed: true,
        clarification_required: true,
        judge_attempts: 1,
      },
    ],
    judge_minimum: 2,
    decision,
    total_model_attempts: 4,
    baseline_fingerprint: null,
    judge_deltas: [],
  };
}
for (const width of [390, 1280]) {
  test(`受控接口：创建刷新定位比较与取消 ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 850 });
    let created = false,
      done = false,
      cancelled = false,
      cancelRequested = false;
    const keys: string[] = [];
    await page.route("**/auth/**", (route) =>
      route.fulfill({
        json: route.request().url().endsWith("/refresh")
          ? {
              access_token: "test-token",
              token_type: "bearer",
              expires_in: 900,
            }
          : { id: "user-a", username: "alice", created_at: time },
      }),
    );
    await page.route("**/v1/threads**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/projects**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/evaluation-jobs**", (route) => {
      const url = new URL(route.request().url()),
        path = url.pathname,
        method = route.request().method();
      if (path.endsWith("/corpora"))
        return route.fulfill({
          json: {
            items: [
              {
                corpus_id: corpus,
                source_ids: ["policy"],
                corpus_fingerprint: "a".repeat(64),
                expires_at: "2099-01-01T00:00:00Z",
              },
            ],
            next_offset: null,
          },
        });
      if (path.endsWith("/validate"))
        return route.fulfill({
          json: {
            status: "valid",
            case_count: 1,
            total_model_limit: 112,
            mode: "controlled",
          },
        });
      if (path === "/v1/evaluation-jobs" && method === "POST") {
        created = true;
        keys.push(route.request().headers()["idempotency-key"]!);
        return route.fulfill({ status: 201, json: { job_id: a } });
      }
      if (path === "/v1/evaluation-jobs")
        return route.fulfill({
          json: {
            items: [
              ...(created
                ? [
                    {
                      job_id: a,
                      dataset_id: "本次实验",
                      status: done ? "completed" : "running",
                      created_at: time,
                      decision: done ? "pass" : null,
                    },
                  ]
                : []),
              {
                job_id: b,
                dataset_id: "参照实验",
                status: "completed",
                created_at: time,
                decision: "pass",
              },
            ],
            next_offset: null,
          },
        });
      if (path.endsWith("/report"))
        return route.fulfill({
          json: { status: "completed", report: artifact(), error_code: null },
        });
      if (path.endsWith("/compare"))
        return route.fulfill({ json: artifact("incomparable") });
      if (path.includes("/cases/"))
        return route.fulfill({
          json: {
            case_id: "meal",
            question: "餐费最多报多少？",
            status: "completed",
            answer: "## 文档依据\n每天150元。[E1]",
            error_code: null,
            judge_error: null,
            counts: { model_attempts: 3, retrieval_calls: 2, tool_calls: 1 },
            materials: [
              {
                material_id: "e1",
                source_id: "policy",
                start: 0,
                end: 9,
                content_sha256: "a".repeat(64),
              },
            ],
            judge: {
              correctness: 2,
              completeness: 2,
              grounding: 2,
              reason: "受控输出",
            },
            requested: true,
            accepted: true,
            resumed: true,
          },
        });
      if (path.endsWith("/cancel")) cancelRequested = true;
      return route.fulfill({
        json: {
          job_id: a,
          status: cancelled ? "cancelled" : done ? "completed" : "running",
          cancel_requested: cancelRequested,
          error_code: null,
          cases: [
            {
              case_id: "meal",
              status: done ? "completed" : "generating",
              error_code: null,
            },
          ],
        },
      });
    });
    await page.goto("/evaluations");
    await page.getByLabel("已准备的评测资料").selectOption(corpus);
    await page.getByLabel("Dataset · 题目与人工评分标准").setInputFiles({
      name: "dataset.json",
      mimeType: "application/json",
      buffer: Buffer.from('{"dataset_id":"test"}'),
    });
    await page.getByLabel("Scenario · 受控输出脚本").setInputFiles({
      name: "scenario.json",
      mimeType: "application/json",
      buffer: Buffer.from('[{"case_id":"meal"}]'),
    });
    await page.getByRole("button", { name: "校验输入", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("校验通过");
    await page.screenshot({
      path: info.outputPath(`evaluation-form-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "创建并运行", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(a));
    await expect(
      page.getByRole("heading", { name: "执行中", exact: true }),
    ).toBeVisible();
    expect(keys).toHaveLength(1);
    expect(keys[0]).toMatch(/^[\da-f-]{36}$/);
    await page.getByRole("button", { name: "请求取消", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "执行中 · 取消中" }),
    ).toBeVisible();
    cancelled = true;
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "已取消", exact: true }),
    ).toBeVisible();
    cancelled = false;
    done = true;
    await page.reload();
    await page.getByRole("button", { name: /meal 已完成/ }).click();
    await expect(
      page.getByRole("heading", { name: "餐费最多报多少？" }),
    ).toBeVisible();
    await expect(page.locator("pre")).toContainText("150元");
    await page.getByLabel("参照实验").selectOption(b);
    await page.getByRole("button", { name: "比较报告", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "比较结果 · 不可比较" }),
    ).toBeVisible();
    await expect(page.getByRole("table")).toHaveCount(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`evaluation-${width}.png`),
      fullPage: true,
    });
  });
}

test("语料服务故障阻止提交，运行故障显示持久错误", async ({ page }) => {
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().endsWith("/refresh")
        ? { access_token: "test-token", token_type: "bearer", expires_in: 900 }
        : { id: "user-a", username: "alice", created_at: time },
    }),
  );
  await page.route("**/v1/threads**", (route) =>
    route.fulfill({ json: { items: [], next_offset: null } }),
  );
  await page.route("**/v1/projects**", (route) =>
    route.fulfill({ json: { items: [], next_offset: null } }),
  );
  await page.route("**/v1/evaluation-jobs**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/corpora"))
      return route.fulfill({ status: 503, json: { detail: "unavailable" } });
    if (path === "/v1/evaluation-jobs")
      return route.fulfill({ json: { items: [], next_offset: null } });
    return route.fulfill({
      json: {
        job_id: a,
        status: "failed",
        cancel_requested: false,
        error_code: "EVALUATION_WORKER_FAILED",
        cases: [
          {
            case_id: "meal",
            status: "failed",
            error_code: "EVALUATION_CASE_FAILED",
          },
        ],
      },
    });
  });
  await page.goto("/evaluations");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "服务或连接异常",
  );
  await expect(
    page.getByRole("button", { name: "创建并运行", exact: true }),
  ).toBeDisabled();
  await page.goto("/evaluations/" + a);
  await expect(
    page.getByRole("heading", { name: "失败", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("main")
      .getByRole("alert")
      .filter({ hasText: "EVALUATION_WORKER_FAILED" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "评测报告" })).toHaveCount(0);
});
