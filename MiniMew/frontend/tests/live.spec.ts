import { test, expect } from "@playwright/test";

test("真实 PostgreSQL + FastAPI + Vite：注册、Cookie 恢复与退出", async ({
  page,
  context,
}) => {
  test.skip(
    !process.env.MINIMEW_LIVE_API,
    "Only the owned temporary database runner enables this test",
  );
  const username = `web_${Date.now()}`;
  await page.goto("/register");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("live-test-password-1234");
  await page.getByRole("button", { name: "创建账户" }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("live-test-password-1234");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(
    page.getByRole("heading", { name: `你好，${username}。` }),
  ).toBeVisible();
  const cookie = (await context.cookies()).find(
    (c) => c.name === "minimew_refresh",
  );
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.path).toBe("/auth");
  expect(cookie?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "minimew_refresh",
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: `你好，${username}。` }),
  ).toBeVisible();
  // 两个同源标签页同时恢复，由 Web Locks 串行轮换 Cookie。
  const second = await context.newPage();
  await Promise.all([page.reload(), second.goto("/account")]);
  await expect(
    page.getByRole("heading", { name: `你好，${username}。` }),
  ).toBeVisible();
  await expect(
    second.getByRole("heading", { name: `你好，${username}。` }),
  ).toBeVisible();
  await second.close();
  await page.getByRole("link", { name: "进入我的对话" }).click();
  await expect(page.getByText("还没有对话，创建第一段吧。")).toBeVisible();
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(
    page.getByRole("heading", { name: "新对话", exact: true }),
  ).toBeVisible();
  const threadUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(threadUrl);
  await expect(
    page.getByRole("heading", { name: "新对话", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  // 同一浏览器切换到另一个新用户，旧列表不能保留，旧 ID 也不可访问。
  await page.goto("/register");
  await page.getByLabel("用户名", { exact: true }).fill(username + "_b");
  await page
    .getByLabel("密码", { exact: true })
    .fill("live-test-password-5678");
  await page.getByRole("button", { name: "创建账户" }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("live-test-password-5678");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(
    page.getByRole("heading", { name: `你好，${username}_b。` }),
  ).toBeVisible();
  await page.goto(threadUrl);
  await expect(
    page.getByRole("heading", { name: "对话不存在或不可访问" }),
  ).toBeVisible();
  await expect(page.getByText("还没有对话，创建第一段吧。")).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(
    (await context.cookies()).find((c) => c.name === "minimew_refresh"),
  ).toBeUndefined();
  await page.goto("/account");
  await expect(page).toHaveURL(/\/login$/);
});
