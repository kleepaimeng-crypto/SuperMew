import { test, expect, type Page } from "@playwright/test";

const a = {
  id: "00000000-0000-0000-0000-000000000001",
  title: "第一段对话",
  created_at: "2026-10-29T00:00:00Z",
};
const b = {
  ...a,
  id: "00000000-0000-0000-0000-000000000002",
  title: "第二段对话",
};
const created = {
  ...a,
  id: "00000000-0000-0000-0000-000000000003",
  title: "新对话",
};
async function identity(page: Page) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "test-token",
        token_type: "bearer",
        expires_in: 900,
      },
    }),
  );
  await page.route("**/auth/me", (route) =>
    route.fulfill({
      json: { id: "user-a", username: "alice", created_at: a.created_at },
    }),
  );
  await page.route("**/auth/logout", (route) => route.fulfill({ status: 204 }));
}

test("列表分页、新建选中、地址恢复与退出", async ({ page }) => {
  await identity(page);
  let added = false;
  await page.route("**/v1/threads**", (route) => {
    expect(route.request().headers().authorization).toBe("Bearer test-token");
    const url = new URL(route.request().url());
    if (route.request().method() === "POST") {
      added = true;
      return route.fulfill({ status: 201, json: created });
    }
    const detail = [a, b, created].find((t) =>
      url.pathname.endsWith("/" + t.id),
    );
    if (detail) return route.fulfill({ json: detail });
    return route.fulfill({
      json: added
        ? { items: [created, a, b], next_offset: null }
        : url.searchParams.get("offset") === "20"
          ? { items: [b], next_offset: null }
          : { items: [a], next_offset: 20 },
    });
  });
  await page.goto("/threads");
  await page.getByRole("button", { name: "加载更多", exact: true }).click();
  await page.getByRole("link", { name: /第二段对话/ }).click();
  await expect(
    page.getByRole("heading", { name: "第二段对话", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page).toHaveURL(new RegExp(`/threads/${created.id}$`));
  await expect(
    page.getByRole("heading", { name: "新对话", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "新对话", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.artifacts/threads-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.artifacts/threads-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("link", { name: /第一段对话/ })).toHaveCount(0);
});

test("创建失败不追加对话；列表故障、不可访问与详情故障可区分", async ({
  page,
}) => {
  await identity(page);
  let listFailed = true;
  let detailStatus = 404;
  let postCount = 0;
  await page.route("**/v1/threads**", (route) => {
    if (route.request().method() === "POST") {
      postCount++;
      return route.fulfill({ status: 500, json: {} });
    }
    if (new URL(route.request().url()).pathname.endsWith(a.id))
      return route.fulfill({
        status: detailStatus,
        json: detailStatus === 200 ? a : {},
      });
    return route.fulfill({
      status: listFailed ? 500 : 200,
      json: listFailed ? {} : { items: [a], next_offset: null },
    });
  });
  await page.goto("/threads");
  await expect(page.getByRole("alert")).toContainText("列表加载失败");
  listFailed = false;
  await page.getByRole("button", { name: "重试加载列表" }).click();
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByRole("alert")).toContainText("创建结果未确认");
  expect(postCount).toBe(1);
  await expect(page.getByRole("listitem")).toHaveCount(1);
  await page.getByRole("link", { name: /第一段对话/ }).click();
  await expect(
    page.getByRole("heading", { name: "对话不存在或不可访问" }),
  ).toBeVisible();
  detailStatus = 500;
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "暂时无法打开对话" }),
  ).toBeVisible();
  detailStatus = 200;
  await page.getByRole("button", { name: "重试打开对话" }).click();
  await expect(
    page.getByRole("heading", { name: "第一段对话", exact: true }),
  ).toBeVisible();
});

test("业务 401 恢复后仍打开原对话地址", async ({ page }) => {
  await identity(page);
  let unauthorized = true;
  await page.route("**/v1/threads**", (route) => {
    const detail = new URL(route.request().url()).pathname.endsWith(a.id);
    return route.fulfill({
      status: unauthorized ? 401 : 200,
      json: unauthorized ? {} : detail ? a : { items: [a], next_offset: null },
    });
  });
  await page.goto(`/threads/${a.id}`);
  await expect(page.getByRole("alert")).toContainText("登录凭据需要更新");
  unauthorized = false;
  await page.getByRole("button", { name: "重新尝试" }).click();
  await expect(page).toHaveURL(new RegExp(`/threads/${a.id}$`));
  await expect(
    page.getByRole("heading", { name: "第一段对话", exact: true }),
  ).toBeVisible();
});
