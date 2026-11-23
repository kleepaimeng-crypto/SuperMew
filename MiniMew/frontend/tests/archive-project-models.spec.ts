import { test, expect, type Page } from "@playwright/test";

const projectId = "11111111-1111-4111-8111-111111111111";
const templateId = "22222222-2222-4222-8222-222222222222";
const threadId = "33333333-3333-4333-8333-333333333333";
const date = "2026-11-21T00:00:00Z";
const cloud = {
  source: "bailian",
  model_name: "text-embedding-v4",
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  key_configured: true,
};
const local = {
  source: "local",
  model_name: "BAAI/bge-reranker-base",
  base_url: null,
  key_configured: false,
};

async function identity(page: Page) {
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().endsWith("/me")
        ? { id: "owner", username: "alice", created_at: date }
        : { access_token: "test-token", token_type: "bearer", expires_in: 900 },
    }),
  );
  await page.route("**/v1/skills", (route) => route.fulfill({ json: [] }));
  await page.route("**/v1/account/model-settings**", (route) =>
    route.fulfill({
      json: route.request().url().endsWith("/presets")
        ? []
        : {
            models: [],
            current_model_id: null,
            knowledge_enabled: true,
            knowledge_available: true,
            credential_storage_ready: true,
            default_model_name: "test-model",
            default_configured: true,
          },
    }),
  );
}

for (const width of [1280, 390]) {
  test(`项目创建绑定、只读配置、独立更新Key ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await identity(page);
    let created = false;
    const writes: unknown[] = [];
    const project = { id: projectId, name: "项目甲", created_at: date };
    await page.route("**/v1/threads**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/retrieval-models", (route) =>
      route.fulfill({
        json: [
          {
            id: templateId,
            name: "百炼向量",
            purpose: "embedding",
            provider: "bailian",
            model_name: cloud.model_name,
            base_url: cloud.base_url,
            key_configured: true,
          },
        ],
      }),
    );
    await page.route("**/v1/projects**", (route) => {
      const request = route.request(),
        url = new URL(request.url());
      if (url.pathname.endsWith("/credential")) {
        writes.push(request.postDataJSON());
        return route.fulfill({ json: { embedding: cloud, rerank: local } });
      }
      if (url.pathname.endsWith("/models"))
        return route.fulfill({ json: { embedding: cloud, rerank: local } });
      if (url.pathname.endsWith("/memories"))
        return route.fulfill({
          json: {
            items: [],
            max_active_items: 20,
            max_content_chars: 500,
            snapshot_max_chars: 12000,
          },
        });
      if (request.method() === "POST") {
        created = true;
        writes.push(request.postDataJSON());
        return route.fulfill({ status: 201, json: project });
      }
      return route.fulfill({
        json:
          url.pathname === "/v1/projects"
            ? { items: created ? [project] : [], next_offset: null }
            : project,
      });
    });
    await page.goto("/projects");
    await page.getByRole("button", { name: "＋ 新建项目" }).click();
    const dialog = page.getByRole("dialog", { name: "新建项目" });
    await dialog.getByLabel("新项目名称").fill("项目甲");
    await dialog
      .getByLabel("Embedding 模型", { exact: true })
      .selectOption(templateId);
    await dialog.getByRole("button", { name: "新建项目", exact: true }).click();
    const panel = page.getByRole("region", {
      name: "项目模型配置",
      exact: true,
    });
    await expect(panel).toContainText("text-embedding-v4");
    expect(writes[0]).toEqual({
      name: "项目甲",
      embedding_configuration_id: templateId,
      rerank_configuration_id: null,
    });
    await expect(panel.locator("select")).toHaveCount(0);
    await panel
      .getByLabel("更新 embedding API Key")
      .fill("fixture-project-key");
    await panel.getByRole("button", { name: "更新此项目的 Key" }).click();
    await expect(panel.getByRole("status")).toContainText("已更新本项目");
    expect(writes[1]).toEqual({ api_key: "fixture-project-key" });
    await expect(panel.getByLabel("更新 embedding API Key")).toHaveValue("");
    await expect(page.locator("body")).not.toContainText("fixture-project-key");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: test.info().outputPath(`project-models-${width}.png`),
      fullPage: true,
    });
  });

  test(`归档保留历史、刷新归档列表与恢复 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await identity(page);
    let archived = false;
    await page.route("**/v1/projects**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/threads**", (route) => {
      const request = route.request(),
        url = new URL(request.url());
      const thread = {
        id: threadId,
        title: "保留的聊天",
        project_id: null,
        created_at: date,
        archived,
      };
      if (url.pathname.endsWith("/messages"))
        return route.fulfill({ json: { items: [], previous_cursor: null } });
      if (request.method() === "PATCH") {
        archived = request.postDataJSON().archived;
        return route.fulfill({ json: { ...thread, archived } });
      }
      if (url.pathname.endsWith(threadId))
        return route.fulfill({ json: thread });
      return route.fulfill({
        json: {
          items:
            (url.searchParams.get("archived") === "true") === archived
              ? [thread]
              : [],
          next_offset: null,
        },
      });
    });
    await page.goto(`/threads/${threadId}`);
    await page.getByRole("button", { name: "归档会话", exact: true }).click();
    await expect(page.getByText("此会话已归档，历史记录保留。")).toBeVisible();
    await page.goto("/archives");
    await expect(
      page.getByRole("link", { name: "保留的聊天", exact: true }).last(),
    ).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "恢复会话", exact: true }).click();
    await expect(page.getByText("当前空间没有已归档聊天。")).toBeVisible();
    await page.goto(`/threads/${threadId}`);
    await expect(
      page.getByRole("button", { name: "归档会话", exact: true }),
    ).toBeVisible();
    expect(archived).toBe(false);
    await page.screenshot({
      path: test.info().outputPath(`archive-restored-${width}.png`),
      fullPage: true,
    });
  });
}

test("账户保存检索配置并修改复用模板，不回显Key", async ({ page }) => {
  await identity(page);
  await page.route("**/v1/projects**", (route) =>
    route.fulfill({ json: { items: [], next_offset: null } }),
  );
  await page.route("**/v1/threads**", (route) =>
    route.fulfill({ json: { items: [], next_offset: null } }),
  );
  let items: unknown[] = [];
  const writes: Record<string, unknown>[] = [];
  await page.route("**/v1/retrieval-models**", (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({ json: items });
    const body = route.request().postDataJSON();
    writes.push(body);
    const { api_key: _, ...safe } = body;
    items = [{ ...safe, id: templateId, key_configured: true }];
    return route.fulfill({
      status: route.request().method() === "POST" ? 201 : 200,
      json: items[0],
    });
  });
  await page.goto("/account");
  await page.getByRole("tab", { name: "检索模型模板" }).click();
  const panel = page.getByRole("region", { name: "可复用检索模型配置" });
  await panel.getByLabel("配置名称").fill("百炼配置");
  await panel.getByLabel("API Key", { exact: true }).fill("fixture-cloud-key");
  await panel.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(panel.getByRole("status")).toHaveText("配置已保存。");
  await panel.getByRole("button", { name: "编辑", exact: true }).click();
  await panel.getByLabel("配置名称").fill("百炼配置新版");
  await panel.getByLabel("API Key", { exact: true }).fill("fixture-new-key");
  await panel.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(panel.getByRole("status")).toHaveText("配置已保存。");
  expect(writes.map((w) => w.api_key)).toEqual([
    "fixture-cloud-key",
    "fixture-new-key",
  ]);
  await expect(panel.getByLabel("API Key", { exact: true })).toHaveValue("");
  await expect(panel).not.toContainText("fixture-new-key");
});
