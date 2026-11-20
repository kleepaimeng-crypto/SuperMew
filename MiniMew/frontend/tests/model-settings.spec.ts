import { test, expect } from "@playwright/test";

for (const width of [1280, 390]) {
  test(`账户模型预设、自定义、当前选择和密钥不回显 ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = {
      models: [] as any[],
      current_model_id: null as string | null,
      knowledge_enabled: true,
      knowledge_available: true,
      credential_storage_ready: true,
      default_model_name: "deepseek-flash",
      default_configured: true,
    };
    const writes: any[] = [];
    const presets = [
      {
        id: "deepseek",
        name: "DeepSeek",
        base_url: "https://api.deepseek.com",
        models: ["deepseek-flash", "deepseek-v4-pro"],
      },
      {
        id: "kimi",
        name: "Kimi / 月之暗面",
        base_url: "https://api.moonshot.cn/v1",
        models: ["kimi-k3.1-pro", "kimi-k3"],
      },
      {
        id: "qwen",
        name: "通义千问 / 阿里云百炼",
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        models: ["qwen3.8-flash"],
      },
    ];
    await page.route("**/auth/**", (route) =>
      route.fulfill({
        json: route.request().url().endsWith("/me")
          ? {
              id: "owner",
              username: "alice",
              created_at: "2026-11-19T00:00:00Z",
            }
          : {
              access_token: "test-token",
              token_type: "bearer",
              expires_in: 900,
            },
      }),
    );
    await page.route("**/v1/threads**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/projects**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/account/model-settings**", async (route) => {
      const req = route.request(),
        path = new URL(req.url()).pathname;
      if (path.endsWith("/presets")) return route.fulfill({ json: presets });
      if (req.method() === "GET") return route.fulfill({ json: state });
      if (path.endsWith("/selection")) {
        state.current_model_id = req.postDataJSON().model_id;
        state.knowledge_enabled = req.postDataJSON().knowledge_enabled;
        return route.fulfill({ status: 204 });
      }
      if (req.method() === "DELETE") {
        const id = path.split("/").pop();
        state.models = state.models.filter((m) => m.id !== id);
        if (state.current_model_id === id) state.current_model_id = null;
        return route.fulfill({ status: 204 });
      }
      const body = req.postDataJSON();
      writes.push(body);
      const id =
        req.method() === "POST"
          ? `model-${state.models.length + 1}`
          : path.split("/").pop();
      const model = { ...body, id, revision: 1, key_configured: true };
      delete model.api_key;
      state.models = state.models.filter((m) => m.id !== id);
      state.models.push(model);
      return route.fulfill({
        status: req.method() === "POST" ? 201 : 200,
        json: model,
      });
    });
    await page.goto("/account");
    await expect(page.getByText("当前使用：")).toContainText("服务端默认");
    await page
      .getByRole("button", { name: "添加模型配置", exact: true })
      .click();
    await page.getByLabel("服务商", { exact: true }).selectOption("kimi");
    await expect(page.getByLabel("模型", { exact: true })).toHaveValue(
      "kimi-k3.1-pro",
    );
    await page.getByLabel("API Key", { exact: true }).fill("fake-local-secret");
    await page.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(
      page.getByText("配置已保存，尚未发起模型调用。"),
    ).toBeVisible();
    expect(writes[0].adapter).toBe("kimi");
    await page.getByRole("button", { name: "设为当前", exact: true }).click();
    await expect(page.getByText("当前使用：")).toContainText("Kimi");
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(page.getByLabel("API Key（留空保留原密钥）")).toHaveValue("");
    await page.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(
      page.getByText("配置已保存，尚未发起模型调用。"),
    ).toBeVisible();
    expect(writes[1].api_key).toBeNull();
    await page
      .getByRole("button", { name: "添加模型配置", exact: true })
      .click();
    await page.getByLabel("服务商", { exact: true }).selectOption("generic");
    expect(
      await page
        .getByLabel("模型", { exact: true })
        .evaluate((el) => getComputedStyle(el, "::placeholder").color),
    ).toBe("rgb(83, 108, 93)");
    await page.screenshot({
      path: `../.impeccable/review/account-models/empty-${width}.png`,
      fullPage: true,
    });
    const customModel =
      width === 390 ? "custom-" + "a".repeat(193) : "custom-text-agent";
    await page.getByLabel("模型", { exact: true }).fill(customModel);
    await page.getByLabel("API Key", { exact: true }).fill("fake-custom-key");
    await page
      .getByLabel("Base URL", { exact: true })
      .fill("https://model.example.com/v1");
    await page.screenshot({
      path: `../.impeccable/review/account-models/form-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(
      page.getByText("配置已保存，尚未发起模型调用。"),
    ).toBeVisible();
    expect(writes.at(-1).model_name).toBe(customModel);
    expect(writes.at(-1).name.length).toBeLessThanOrEqual(80);
    await page.getByLabel("允许新任务检索当前范围的资料").uncheck();
    await page.getByRole("button", { name: "保存使用设置" }).click();
    await expect(
      page.getByText("使用设置已更新，将用于之后的新任务。"),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByLabel("允许新任务检索当前范围的资料"),
    ).not.toBeChecked();
    await expect(page.getByText("当前使用：")).toContainText("Kimi");
    expect(
      await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])),
    ).not.toContain("fake-");
    expect(await page.locator("body").innerText()).not.toContain(
      "fake-local-secret",
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../.impeccable/review/account-models/saved-${width}.png`,
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "删除", exact: true })
      .first()
      .click();
    await page.getByRole("button", { name: "确认删除", exact: true }).click();
    await expect(page.getByText("当前使用：")).toContainText("服务端默认");
  });
}

test("设置读取失败可重试，未配置密钥存储不能提交", async ({ page }) => {
  let failed = true;
  await page.route("**/auth/**", (route) =>
    route.fulfill({
      json: route.request().url().endsWith("/me")
        ? { id: "owner", username: "alice", created_at: "2026-11-19T00:00:00Z" }
        : { access_token: "test-token", token_type: "bearer", expires_in: 900 },
    }),
  );
  await page.route("**/v1/threads**", (route) =>
    route.fulfill({ json: { items: [], next_offset: null } }),
  );
  await page.route("**/v1/projects**", (route) =>
    route.fulfill({ json: { items: [], next_offset: null } }),
  );
  await page.route("**/v1/account/model-settings**", (route) =>
    route.fulfill({
      status: failed ? 503 : 200,
      json: failed
        ? {}
        : route.request().url().endsWith("/presets")
          ? []
          : {
              models: [],
              current_model_id: null,
              knowledge_enabled: true,
              knowledge_available: false,
              credential_storage_ready: false,
              default_model_name: "deepseek-flash",
              default_configured: false,
            },
    }),
  );
  await page.goto("/account");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "模型设置加载失败",
  );
  await page.screenshot({
    path: "../.impeccable/review/account-models/load-error.png",
    fullPage: true,
  });
  failed = false;
  await page.getByRole("button", { name: "刷新设置" }).click();
  await expect(page.getByText("密钥存储尚未就绪")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "添加模型配置", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: "../.impeccable/review/account-models/storage-unavailable.png",
    fullPage: true,
  });
});
