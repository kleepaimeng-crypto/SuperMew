import { expect, test } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";

test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 } });

test("真实文档：上传发布、坏新版、刷新恢复与账户隔离", async ({ page }) => {
  test.skip(
    !process.env.MINIMEW_DOCUMENT_LIVE,
    "Only the owned real CPU/Milvus runner enables this test",
  );
  test.setTimeout(120000);
  mkdirSync("../.impeccable/review", { recursive: true });
  await page.goto("/register");
  await page.getByLabel("用户名", { exact: true }).fill(`docs_${Date.now()}`);
  await page
    .getByLabel("密码", { exact: true })
    .fill("browser-doc-password-1234");
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("browser-doc-password-1234");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "上传一份文档", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("还没有文档。", { exact: false })).toBeVisible();
  await page.screenshot({
    path: "../.impeccable/review/empty.png",
    fullPage: true,
  });
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "报销制度.md",
    mimeType: "text/markdown",
    buffer: readFileSync("../examples/rag/expense-policy.md"),
  });
  await page.getByRole("button", { name: "上传并构建", exact: true }).click();
  await expect(page).toHaveURL(/\/documents\/[a-f0-9-]+$/);
  const url = page.url();
  const published = page.getByRole("region", {
    name: "当前可用版本",
    exact: true,
  });
  await expect(published).toContainText("已发布", { timeout: 60000 });
  await expect(published).toContainText("v1");
  await page.reload();
  await expect(published).toContainText("已发布");
  await expect(
    page.getByText("bge-small-zh-v1.5 · 512 维", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.impeccable/review/published.png",
    fullPage: true,
  });
  await page.getByLabel("新版文件", { exact: true }).setInputFiles({
    name: "报销制度新版.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("```\n" + "long ".repeat(900) + "\n```\n"),
  });
  await page.getByRole("button", { name: "提交新版构建", exact: true }).click();
  await expect(page.getByText("构建失败", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  await expect(published).toContainText("v1");
  await expect(published).toContainText("当前版本仍可检索");
  await page.reload();
  await expect(page.getByText("构建失败", { exact: true })).toBeVisible();
  await expect(published).toContainText("v1");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../.impeccable/review/desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../.impeccable/review/mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await page.goto("/register");
  await page.getByLabel("用户名", { exact: true }).fill(`other_${Date.now()}`);
  await page
    .getByLabel("密码", { exact: true })
    .fill("browser-doc-password-1234");
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("browser-doc-password-1234");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await expect(page.getByText("还没有文档。", { exact: false })).toBeVisible();
  await page.goto(url);
  await expect(page.getByRole("alert")).toContainText("无权访问");
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain("access_token");
});
