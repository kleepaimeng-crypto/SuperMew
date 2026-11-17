import { test, expect } from "@playwright/test";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec = promisify(execFile);
test("真实API/PG/Milvus/CPU Worker：工作台创建、刷新、报告、比较、取消与账号隔离", async ({
  page,
}, info) => {
  test.skip(
    process.env.MINIMEW_EVALUATION_BROWSER_TESTS !== "1",
    "Explicit owned evaluation browser runner required",
  );
  test.setTimeout(240000);
  const env = process.env;
  await page.goto("/login");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(env.MINIMEW_EVAL_USERNAME!);
  await page
    .getByLabel("密码", { exact: true })
    .fill(env.MINIMEW_EVAL_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "评测", exact: true }).click();
  async function create() {
    await page.getByRole("button", { name: "新建实验", exact: true }).click();
    await page
      .getByLabel("已准备的评测资料")
      .selectOption(env.MINIMEW_EVAL_CORPUS!);
    await page
      .getByLabel("Dataset · 题目与人工评分标准")
      .setInputFiles(env.MINIMEW_EVAL_DATASET!);
    await page
      .getByLabel("Scenario · 受控输出脚本")
      .setInputFiles(env.MINIMEW_EVAL_SCENARIOS!);
    await page
      .getByText("每题执行预算（秒）", { exact: true })
      .locator("input")
      .fill("300");
    await page.getByRole("button", { name: "校验输入", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("校验通过");
    await page.getByRole("button", { name: "创建并运行", exact: true }).click();
    await expect(page).toHaveURL(/evaluations\/[\da-f-]{36}$/);
    return page.url().split("/").at(-1)!;
  }
  async function worker(phase = "completed") {
    await exec(
      env.MINIMEW_EVAL_PYTHON!,
      ["scripts/evaluation_process.py", phase],
      {
        cwd: env.MINIMEW_EVAL_ROOT,
        env,
        timeout: 150000,
        maxBuffer: 2_000_000,
      },
    );
  }
  const baseline = await create();
  const first = worker();
  await page.reload();
  await first;
  await expect(
    page.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await page.getByRole("button", { name: /meal 已完成/ }).click();
  await expect(page.locator("pre")).toContainText("150元");
  await expect(page.getByRole("article")).toContainText("已续接：是");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "实验报告 · 通过" }),
  ).toBeVisible();
  const candidate = await create();
  expect(candidate).not.toBe(baseline);
  await worker();
  await expect(
    page.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await page.getByLabel("参照实验").selectOption(baseline);
  await page.getByRole("button", { name: "比较报告", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "比较结果 · 通过" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("evaluation-real-report.png"),
    fullPage: true,
  });
  const cancelled = await create();
  await page.getByRole("button", { name: "请求取消", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /排队中 · 取消中/ }),
  ).toBeVisible();
  await worker("cancelled");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "已取消", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "账户", exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await page.goto("/register");
  const other = "eval_other_" + Date.now();
  await page.getByLabel("用户名", { exact: true }).fill(other);
  await page
    .getByLabel("密码", { exact: true })
    .fill("owned-evaluation-other-1234");
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("owned-evaluation-other-1234");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "评测", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "实验历史" }).getByRole("link"),
  ).toHaveCount(0);
  await expect(
    page.getByText("暂无可用资料。", { exact: false }),
  ).toBeVisible();
  await page.goto("/evaluations/" + cancelled);
  await expect(page.getByRole("alert")).toContainText("记录不存在");
});
