import { test, expect, type Page } from "@playwright/test";

const time = "2026-11-20T00:00:00Z";
const projectId = "11111111-1111-4111-8111-111111111111";
const personalThread = {
  id: "00000000-0000-0000-0000-000000000001",
  title: "个人老会话",
  created_at: time,
};
const projectThread = {
  id: "00000000-0000-0000-0000-000000000002",
  project_id: projectId,
  title: "项目老会话",
  created_at: time,
};
const createdProjectThread = {
  id: "00000000-0000-0000-0000-000000000003",
  project_id: projectId,
  title: "新项目会话",
  created_at: time,
};
const project = {
  id: projectId,
  name: "研发知识库",
  created_at: time,
};

async function setupWorkspaceRoutes(
  page: Page,
  options: { failFirstRunPost?: boolean } = {},
) {
  let threadCreateCount = 0;
  let runCreateCount = 0;
  let created = false;
  const projectThreadBodies: unknown[] = [];

  await page.route("**/auth/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/refresh") || path.endsWith("/login")) {
      return route.fulfill({
        json: {
          access_token: "ws-token",
          token_type: "bearer",
          expires_in: 900,
        },
      });
    }
    if (path.endsWith("/me")) {
      return route.fulfill({
        json: { id: "user-a", username: "alice", created_at: time },
      });
    }
    return route.fulfill({ status: 204 });
  });

  await page.route("**/v1/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    if (path === "/v1/skills") return route.fulfill({ json: [] });
    if (path === "/v1/projects" && method === "GET") {
      return route.fulfill({ json: { items: [project], next_offset: null } });
    }
    if (path === `/v1/projects/${projectId}` && method === "GET") {
      return route.fulfill({ json: project });
    }
    if (path === `/v1/projects/${projectId}/memories` && method === "GET") {
      return route.fulfill({ json: { items: [] } });
    }
    if (path === "/v1/documents" && method === "GET") {
      return route.fulfill({ json: { items: [], next_offset: null } });
    }
    if (path === "/v1/documents/embedding-profile") {
      return route.fulfill({ status: 404, json: {} });
    }
    if (path === "/v1/evaluations/jobs" || path === "/v1/evaluations/corpora") {
      return route.fulfill({ json: { items: [], next_offset: null } });
    }
    if (path === "/v1/threads" && method === "POST") {
      threadCreateCount++;
      projectThreadBodies.push(route.request().postDataJSON());
      created = true;
      return route.fulfill({ status: 201, json: createdProjectThread });
    }
    if (path === "/v1/threads" && method === "GET") {
      const pid = url.searchParams.get("project_id");
      if (pid === projectId) {
        return route.fulfill({
          json: {
            items: created
              ? [createdProjectThread, projectThread]
              : [projectThread],
            next_offset: null,
          },
        });
      }
      return route.fulfill({
        json: { items: [personalThread], next_offset: null },
      });
    }
    if (path === `/v1/threads/${createdProjectThread.id}` && method === "GET") {
      return route.fulfill({ json: createdProjectThread });
    }
    if (path === `/v1/threads/${projectThread.id}` && method === "GET") {
      return route.fulfill({ json: projectThread });
    }
    if (path === `/v1/threads/${personalThread.id}` && method === "GET") {
      return route.fulfill({ json: personalThread });
    }
    if (path.endsWith("/messages")) {
      return route.fulfill({ json: { items: [], previous_cursor: null } });
    }
    if (path.endsWith("/runs") && method === "POST") {
      runCreateCount++;
      if (options.failFirstRunPost && runCreateCount === 1) {
        return route.abort("failed");
      }
      return route.fulfill({
        status: 201,
        json: {
          id: "00000000-0000-0000-0000-000000000091",
          thread_id: createdProjectThread.id,
          user_message_id: "00000000-0000-0000-0000-000000000092",
          assistant_message_id: "00000000-0000-0000-0000-000000000093",
          status: "succeeded",
          created_at: time,
          finished_at: time,
        },
      });
    }
    return route.fulfill({ status: 404, json: {} });
  });

  return {
    getThreadCreateCount: () => threadCreateCount,
    getRunCreateCount: () => runCreateCount,
    getProjectThreadBodies: () => projectThreadBodies,
  };
}

test("10.1-10.4：登录进新会话、项目展开不串范围、文档跟随范围、评测保持全局与窄屏抽屉", async ({
  page,
}) => {
  const stats = await setupWorkspaceRoutes(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/threads$/);
  await expect(page.getByRole("heading", { name: "开始新会话" })).toBeVisible();

  // 个人空间默认展开，显示个人会话
  await expect(page.getByRole("link", { name: /个人老会话/ })).toBeVisible();
  // 空草稿发送按钮禁用，不创建 Thread
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(stats.getThreadCreateCount()).toBe(0);

  // 点击项目名展开项目会话（不跳转概览），项目概览有独立入口
  await page.getByRole("button", { name: "研发知识库", exact: true }).click();
  await expect(page.getByRole("link", { name: /项目老会话/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /个人老会话/ })).toBeVisible();

  // 一级入口“文档”跟随当前项目并标明范围
  const docNav = page
    .getByRole("navigation", { name: "一级导航" })
    .getByRole("link", { name: /文档/ });
  await expect(docNav).toContainText("研发知识库");
  await docNav.click();
  await expect(page).toHaveURL(
    new RegExp(`/documents\\?project=${projectId}$`),
  );
  await expect(page.getByText("项目知识库 · 研发知识库")).toBeVisible();

  // 切回个人文档
  await page.getByRole("link", { name: "切换个人文档" }).click();
  await expect(page).toHaveURL(/\/documents$/);
  await expect(page.getByText("个人知识库")).toBeVisible();

  // 点击项目概览入口进入项目页，离开后左栏项目仍保留
  await page.getByRole("link", { name: "研发知识库 概览与设置" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await expect(page.getByRole("heading", { name: "研发知识库" })).toBeVisible();

  // 一级入口“评测”保持全局，不带 project 参数
  await page
    .getByRole("navigation", { name: "一级导航" })
    .getByRole("link", { name: /评测/ })
    .click();
  await expect(page).toHaveURL(/\/evaluations$/);
  await expect(page.getByText(/全局评测工作台/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "研发知识库", exact: true }),
  ).toBeVisible();

  // 桌面与窄屏抽屉验证无横向溢出
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航栏" }).click();
  await expect(
    page.getByRole("link", { name: /新会话/ }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭导航栏" }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("10.3：项目新会话首发创建，阶段B失败转交原Thread重试且不重复建Thread", async ({
  page,
}) => {
  const stats = await setupWorkspaceRoutes(page, { failFirstRunPost: true });
  await page.goto(`/threads?project=${projectId}`);
  await expect(page.getByText("项目 · 研发知识库")).toBeVisible();

  await page.getByLabel("你的问题").fill("项目里的第一条问题");
  await page.getByRole("button", { name: "发送问题" }).click();

  // 阶段 A 成功创建 Thread，阶段 B 失败后自动落到该 Thread 并显示重试原提交
  await expect(page).toHaveURL(
    new RegExp(`/threads/${createdProjectThread.id}\\?project=${projectId}$`),
  );
  expect(stats.getThreadCreateCount()).toBe(1);
  expect(stats.getProjectThreadBodies()).toEqual([{ project_id: projectId }]);
  expect(stats.getRunCreateCount()).toBe(1);

  await expect(page.getByRole("button", { name: "重试原提交" })).toBeVisible();
  await page.getByRole("button", { name: "重试原提交" }).click();
  await expect(page.getByRole("button", { name: "发送问题" })).toBeVisible();
  // 重试只重发 Run，绝不重复创建 Thread
  expect(stats.getThreadCreateCount()).toBe(1);
  expect(stats.getRunCreateCount()).toBe(2);
});
