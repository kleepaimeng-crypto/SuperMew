import { expect, test } from "@playwright/test";

test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 } });

test("真实知识链：引用、工具重放、新版隔离、取消与空资料", async ({ page }) => {
  test.skip(
    !process.env.MINIMEW_KNOWLEDGE_BROWSER_TESTS,
    "Owned CPU/Milvus browser runner required",
  );
  test.setTimeout(150000);
  page.setDefaultTimeout(15000);
  const password = "knowledge-browser-password-1234";
  async function loginNew(suffix: string) {
    await page.goto("/register");
    await page
      .getByLabel("用户名", { exact: true })
      .fill(`rag_${Date.now()}_${suffix}`);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "创建账户", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("账户已创建");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
  }
  async function ask(text: string) {
    await page.getByLabel("你的问题", { exact: true }).fill(text);
    await page.getByRole("button", { name: "发送问题", exact: true }).click();
  }
  await loginNew("a");
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "差旅制度.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# 差旅制度\n\n## 餐费\n出差餐费每人每天最高报销150元，须提供票据。",
    ),
  });
  await page.getByRole("button", { name: "上传并构建", exact: true }).click();
  await expect(page).toHaveURL(/\/documents\/[a-f0-9-]+$/);
  const docsUrl = page.url();
  const published = page.getByRole("region", {
    name: "当前可用版本",
    exact: true,
  });
  await expect(published).toContainText("已发布", { timeout: 60000 });
  await page.getByRole("link", { name: "对话", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建对话", exact: true }).click();
  await expect(page).toHaveURL(/\/threads\/[a-f0-9-]+$/);
  const chatUrl = page.url();
  await ask("餐费能报销多少？");
  const first = page.getByRole("article", { name: "MiniMew 回复" }).first();
  await expect(
    first.getByRole("button", { name: "查看引用 E1", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await first.getByRole("button", { name: "查看引用 E1", exact: true }).click();
  await expect(
    first.getByRole("region", { name: "引用原文", exact: true }),
  ).toContainText("150元");
  await first.locator("summary").click();
  await expect(first.locator(".tool-activity")).toContainText("查询知识库");
  await expect(first.locator(".tool-state")).toHaveText("已完成");
  await page.reload();
  await first.getByRole("button", { name: "查看引用 E1", exact: true }).click();
  await expect(
    first.getByRole("region", { name: "引用原文", exact: true }),
  ).toContainText("150元");
  await first.locator("summary").click();
  await expect(first.locator(".tool-state")).toHaveText("已完成");
  await page.screenshot({
    path: "../.artifacts/knowledge-desktop.png",
    fullPage: true,
  });
  await page.goto(docsUrl);
  await page.getByLabel("新版文件", { exact: true }).setInputFiles({
    name: "差旅制度新版.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# 差旅制度\n\n## 餐费\n出差餐费每人每天最高报销200元，须提供票据。",
    ),
  });
  await page.getByRole("button", { name: "提交新版构建", exact: true }).click();
  await expect(published).toContainText("v2", { timeout: 60000 });
  await page.goto(chatUrl);
  await first.getByRole("button", { name: "查看引用 E1", exact: true }).click();
  await expect(
    first.getByRole("region", { name: "引用原文", exact: true }),
  ).toContainText("150元");
  await ask("再查询餐费标准");
  const second = page.getByRole("article", { name: "MiniMew 回复" }).nth(1);
  await second
    .getByRole("button", { name: "查看引用 E1", exact: true })
    .click();
  await expect(
    second.getByRole("region", { name: "引用原文", exact: true }),
  ).toContainText("200元");
  await page.setViewportSize({ width: 390, height: 844 });
  await second.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.artifacts/knowledge-mobile.png",
    fullPage: true,
  });
  await ask("取消这次查询");
  const third = page.getByRole("article", { name: "MiniMew 回复" }).nth(2);
  await third.getByRole("button", { name: "停止回复", exact: true }).click();
  await expect(third.getByRole("status").first()).toHaveText("已取消");
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await loginNew("b");
  await page.getByRole("link", { name: "对话", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建对话", exact: true }).click();
  await ask("餐费能报销多少？");
  await expect(
    page.getByText("本次没有文档依据，无法确认公司的报销标准。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /查看引用/ })).toHaveCount(0);
});
