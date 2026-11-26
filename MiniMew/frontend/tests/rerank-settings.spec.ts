import { expect, test } from "@playwright/test";
import type { RerankSettings } from "../src/documents";

const projectId = "3a9d64b1-9c98-4fdc-99bb-1309fff6df50";

for (const width of [1280, 390]) {
  test(`重排保存作用于当前知识库 ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const local: RerankSettings = {
      source: "local",
      model: "BAAI/bge-reranker-base",
      bailian_key_configured: false,
      local_models_enabled: true,
    };
    let project = local;
    await page.route("**/auth/**", (route) =>
      route.fulfill({
        json: route.request().url().endsWith("/me")
          ? {
              id: projectId,
              username: "alice",
              created_at: "2026-11-20T00:00:00Z",
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
    await page.route("**/v1/documents**", (route) =>
      route.fulfill({ json: { items: [], next_offset: null } }),
    );
    await page.route("**/v1/embedding-profiles/selection**", (route) =>
      route.fulfill({
        json: {
          current_model: "BAAI/bge-small-zh-v1.5",
          current_source: "local",
          dimensions: 512,
          rebuild: null,
          bailian_key_configured: false,
        },
      }),
    );
    await page.route("**/v1/rerank-settings**", (route) => {
      const request = route.request();
      const id = new URL(request.url()).searchParams.get("project_id");
      if (request.method() === "GET")
        return route.fulfill({ json: id ? project : local });
      expect(id).toBe(projectId);
      expect(request.method()).toBe("PUT");
      expect(request.postDataJSON()).toEqual({
        source: "bailian",
        api_key: "fake-rerank-key",
      });
      project = {
        source: "bailian",
        model: "qwen3-rerank",
        bailian_key_configured: true,
        local_models_enabled: true,
      };
      return route.fulfill({ json: project });
    });

    await page.goto(`/documents?project=${projectId}`);
    const panel = page.getByRole("region", { name: "知识库重排" });
    await expect(
      panel.getByText("重排模型 BAAI/bge-reranker-base"),
    ).toBeVisible();
    await panel.getByText("更换重排模型", { exact: true }).click();
    await panel.getByLabel("重排来源").selectOption("bailian");
    await expect(panel.getByText("候选片段会发送到百炼")).toBeVisible();
    await panel.getByLabel("百炼重排 API Key").fill("fake-rerank-key");
    await panel.screenshot({ path: info.outputPath("rerank-form.png") });
    await panel.getByRole("button", { name: "保存重排设置" }).click();
    await expect(panel.getByText("从新对话请求开始生效")).toBeVisible();
    await expect(panel.getByLabel("百炼重排 API Key")).toHaveValue("");
    await expect(page.getByLabel("文档文件")).toBeEnabled();
    await page.reload();
    await expect(panel.getByText("重排模型 qwen3-rerank")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.goto("/documents");
    await expect(
      panel.getByText("重排模型 BAAI/bge-reranker-base"),
    ).toBeVisible();
    expect(
      await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage }),
      ),
    ).not.toContain("fake-rerank-key");
  });
}
