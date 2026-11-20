import { expect, test } from "@playwright/test";
import type { EmbeddingSettings } from "../src/documents";

const projectId = "3a9d64b1-9c98-4fdc-99bb-1309fff6df50";
const taskId = "2a9d64b1-9c98-4fdc-99bb-1309fff6df50";

for (const width of [1280, 390]) {
  test(`知识库编码按范围重建并在刷新后恢复 ${width}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const baseline: EmbeddingSettings = {
      current_model: "BAAI/bge-small-zh-v1.5",
      current_source: "local",
      dimensions: 512,
      bailian_key_configured: false,
      rebuild: null,
    };
    let project = baseline;
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
    await page.route("**/v1/embedding-profiles/selection**", async (route) => {
      const request = route.request();
      const id = new URL(request.url()).searchParams.get("project_id");
      if (request.method() === "GET")
        return route.fulfill({ json: id === projectId ? project : baseline });
      expect(id).toBe(projectId);
      expect(request.postDataJSON()).toEqual({
        source: "bailian",
        api_key: "fixture-key",
      });
      project = {
        ...baseline,
        bailian_key_configured: true,
        rebuild: {
          id: taskId,
          status: "building",
          target_model: "text-embedding-v4",
          total: 2,
          completed: 1,
          error_code: null,
        },
      };
      return route.fulfill({ status: 202, json: project });
    });

    await page.goto(`/documents?project=${projectId}`);
    await expect(page.getByText("当前使用")).toContainText(
      "BAAI/bge-small-zh-v1.5",
    );
    await page.getByText("更换编码模型", { exact: true }).click();
    await page.getByLabel("目标模型").selectOption("bailian");
    await expect(page.getByText("会发送到百炼")).toBeVisible();
    await page.getByLabel("百炼 API Key").fill("fixture-key");
    await page.screenshot({ path: info.outputPath("embedding-form.png") });
    await page.getByRole("button", { name: "重建此知识库索引" }).click();
    await expect(page.getByText("已核验 1 / 2 份文档")).toBeVisible();
    await expect(page.getByLabel("文档文件")).toBeDisabled();
    await page.reload();
    await expect(page.getByText("已核验 1 / 2 份文档")).toBeVisible();
    await page.screenshot({ path: info.outputPath("embedding-progress.png") });
    project = {
      ...project,
      current_model: "text-embedding-v4",
      current_source: "bailian",
      rebuild: { ...project.rebuild!, status: "succeeded", completed: 2 },
    };
    await expect(page.getByText("当前使用")).toContainText("text-embedding-v4");
    await expect(page.getByText("索引重建完成")).toBeVisible();
    await expect(page.getByLabel("文档文件")).toBeEnabled();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.goto("/documents");
    await expect(page.getByText("当前使用")).toContainText(
      "BAAI/bge-small-zh-v1.5",
    );
    await expect(page.getByText("索引重建完成")).toHaveCount(0);
    expect(
      await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage }),
      ),
    ).not.toContain("fixture-key");
  });
}
