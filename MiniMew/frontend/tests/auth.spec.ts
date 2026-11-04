import { test, expect } from "@playwright/test";

const profile = {
  id: "test-user",
  username: "alice",
  created_at: "2026-10-29T00:00:00Z",
};
const token = {
  access_token: "browser-test-token",
  token_type: "bearer",
  expires_in: 900,
};

test("注册、登录、刷新恢复、退出；凭据不写入存储", async ({ page }) => {
  let loggedIn = false;
  await page.route("**/auth/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/register")
      return route.fulfill({ status: 201, json: profile });
    if (path === "/auth/login") {
      loggedIn = true;
      return route.fulfill({ json: token });
    }
    if (path === "/auth/refresh")
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn ? token : {},
      });
    if (path === "/auth/me") {
      expect(route.request().headers().authorization).toBe(
        "Bearer browser-test-token",
      );
      return route.fulfill({ json: profile });
    }
    if (path === "/auth/logout") {
      loggedIn = false;
      return route.fulfill({ status: 204 });
    }
    await route.abort();
  });
  await page.goto("/account");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("link", { name: "创建账户", exact: true }).click();
  await page.getByLabel("用户名", { exact: true }).fill("alice");
  await page.getByLabel("密码", { exact: true }).fill("test-password-1234");
  await page.getByRole("button", { name: "创建账户" }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page.getByLabel("密码", { exact: true }).fill("test-password-1234");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(
    page.getByRole("heading", { name: "你好，alice。" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "你好，alice。" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => ({
      local: { ...localStorage },
      session: { ...sessionStorage },
    })),
  ).toEqual({ local: {}, session: {} });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "登录 MiniMew" }),
  ).toBeVisible();
});

test("恢复失败不跳登录，重试成功后才显示账户；移动端无横向溢出", async ({
  page,
}) => {
  let fail = true;
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({ status: fail ? 500 : 200, json: fail ? {} : token }),
  );
  await page.route("**/auth/me", (route) => route.fulfill({ json: profile }));
  await page.goto("/account");
  await expect(page.getByRole("alert")).toContainText("服务暂时不可用");
  await expect(page).toHaveURL(/\/account$/);
  fail = false;
  await page.getByRole("button", { name: "重新尝试" }).click();
  await expect(
    page.getByRole("heading", { name: "你好，alice。" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("登录页桌面和移动布局", async ({ page }) => {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({ status: 401, json: {} }),
  );
  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "登录 MiniMew" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.artifacts/login-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.artifacts/login-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("退出失败后刷新页面优先重试退出，不恢复登录", async ({ page }) => {
  let refreshCalls = 0;
  let failLogout = true;
  await page.route("**/auth/refresh", (route) => {
    refreshCalls++;
    return route.fulfill({ json: token });
  });
  await page.route("**/auth/me", (route) => route.fulfill({ json: profile }));
  await page.route("**/auth/logout", (route) =>
    route.fulfill({ status: failLogout ? 500 : 204 }),
  );
  await page.goto("/account");
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(
    page.getByRole("heading", { name: "退出尚未完成" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "退出尚未完成" }),
  ).toBeVisible();
  expect(refreshCalls).toBe(1);
  failLogout = false;
  await page.getByRole("button", { name: "重试退出" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(
    await page.evaluate(() => sessionStorage.getItem("minimew.logout-pending")),
  ).toBeNull();
});

test("恢复等待期间不请求用户数据或显示登录表单", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let meCalls = 0;
  await page.route("**/auth/refresh", async (route) => {
    await pending;
    await route.fulfill({ json: token });
  });
  await page.route("**/auth/me", (route) => {
    meCalls++;
    return route.fulfill({ json: profile });
  });
  await page.goto("/account");
  await expect(
    page.getByRole("heading", { name: "正在恢复登录" }),
  ).toBeVisible();
  expect(meCalls).toBe(0);
  await expect(page.getByLabel("密码", { exact: true })).toHaveCount(0);
  release();
  await expect(
    page.getByRole("heading", { name: "你好，alice。" }),
  ).toBeVisible();
});
