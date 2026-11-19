import { test, expect } from "@playwright/test";
test("真实项目/文档索引隔离、手工记忆与下线历史引用", async ({
  page,
}, info) => {
  test.skip(
    process.env.MINIMEW_PROJECT_BROWSER_TESTS !== "1",
    "Owned project runner only",
  );
  test.setTimeout(240000);
  page.on("response", (r) => {
    if (r.status() >= 400)
      console.log("HTTP", r.status(), new URL(r.url()).pathname);
  });
  page.on("requestfailed", (r) =>
    console.log("NETWORK", new URL(r.url()).pathname, r.failure()?.errorText),
  );
  page.on("dialog", (dialog) => dialog.accept());
  const username = "project_" + Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("project-test-password-1234");
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("project-test-password-1234");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "项目", exact: true }).click();
  async function project(name: string) {
    await page.getByLabel("新项目名称").fill(name);
    await page.getByRole("button", { name: "新建项目", exact: true }).click();
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
    return page.url().split("/").at(-1)!;
  }
  async function upload(name: string, text: string) {
    await page.getByLabel("文档文件").setInputFiles({
      name,
      mimeType: "text/markdown",
      buffer: Buffer.from(text),
    });
    await page.getByRole("button", { name: "上传并构建", exact: true }).click();
    await expect(page.locator(".published-state")).toContainText("已发布", {
      timeout: 90000,
    });
    return page.url().split("/").at(-1)!.split("?")[0]!;
  }
  const first = await project("论文项目");
  await page
    .getByLabel("保存重要信息")
    .fill("项目手工记忆测试：只研究国内制度");
  await page.getByRole("button", { name: "保存项目记忆", exact: true }).click();
  await expect(page.getByLabel("手工记忆 · 版本 1")).toHaveValue(
    "项目手工记忆测试：只研究国内制度",
  );
  await page.getByRole("link", { name: "管理项目文档" }).click();
  const doc = await upload("paper.md", "# 论文项目制度\n国内出差餐费150元。");
  await page.goto("/projects/" + first);
  await page.getByRole("link", { name: "打开项目对话" }).click();
  await page.getByRole("button", { name: /新建对话/ }).click();
  await page.getByLabel("你的问题").fill("餐费上限是多少？");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  const chat = page.url();
  const answer = page
    .getByRole("article", { name: "MiniMew 回复", exact: true })
    .first();
  await expect(answer).toContainText("150元", { timeout: 45000 });
  await answer
    .getByRole("button", { name: "查看引用 E1", exact: true })
    .click();
  await expect(answer.getByRole("region", { name: "引用原文" })).toContainText(
    "论文项目制度",
  );
  await answer.locator(".tool-activity > summary").click();
  await expect(answer.locator(".process-tasks")).toContainText("paper.md");
  await expect(answer.getByText("向量召回", { exact: true })).not.toBeVisible();
  await answer.getByText("检索详情", { exact: true }).first().click();
  await expect(answer.locator(".process-tasks")).toContainText("餐费");
  await expect(answer.locator(".process-tasks")).toContainText("向量召回");
  await expect(answer.locator(".process-tasks")).toContainText("论文项目制度");
  await page.screenshot({
    path: info.outputPath("process-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 850 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("process-mobile.png"),
    fullPage: true,
  });
  await answer
    .locator(".process-tasks")
    .getByText("向量召回", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("mobile-steps.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await answer
    .locator(".process-tasks")
    .getByText("向量召回", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("desktop-steps.png"),
    fullPage: true,
  });
  await page.reload();
  await answer.locator(".tool-activity > summary").click();
  await expect(answer.locator(".process-tasks")).toContainText("paper.md");
  await answer.getByText("检索详情", { exact: true }).first().click();
  await expect(answer.locator(".process-tasks")).toContainText("论文项目制度");
  await page.goto("/projects");
  const second = await project("报销项目");
  await page.getByRole("link", { name: "管理项目文档" }).click();
  await expect(page.locator(".document-list li")).toHaveCount(0);
  await upload("expense.md", "# 报销项目制度\n餐费999元。");
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await expect(page.locator(".document-list li")).toHaveCount(0);
  await upload("personal.md", "# 个人制度\n餐费77元。");
  await page.goto(`/documents?project=${first}`);
  await expect(page.locator(".document-list")).toContainText("paper.md");
  await expect(page.locator(".document-list")).not.toContainText("expense.md");
  await expect(page.locator(".document-list")).not.toContainText("personal.md");
  await page.getByRole("link", { name: /paper.md/ }).click();
  await page.getByRole("button", { name: "下线文档", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "文档已下线" }),
  ).toContainText("文档已下线");
  await page.reload();
  await expect(
    page.getByRole("status").filter({ hasText: "文档已下线" }),
  ).toContainText("文档已下线");
  await expect(
    page.getByRole("button", { name: "上传并构建", exact: true }),
  ).toHaveCount(0);
  await page.goto(chat);
  const historical = page
    .getByRole("article", { name: "MiniMew 回复", exact: true })
    .first();
  await expect(historical).toContainText("150元");
  await historical
    .getByRole("button", { name: "查看引用 E1", exact: true })
    .click();
  await expect(
    historical.getByRole("region", { name: "引用原文" }),
  ).toContainText("此文档已下线");
  await expect(
    historical.getByRole("region", { name: "引用原文" }),
  ).toContainText("150元");
  await page.getByLabel("你的问题").fill("重新检索餐费规则");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(
    page.getByRole("article", { name: "MiniMew 回复", exact: true }).last(),
  ).toContainText("本次没有文档依据", { timeout: 45000 });
  await page.goto("/projects/" + second);
  await expect(page.getByText("暂未保存记忆。", { exact: true })).toBeVisible();
  await page.goto("/projects/" + first);
  await expect(page.getByLabel("手工记忆 · 版本 1")).toHaveValue(
    "项目手工记忆测试：只研究国内制度",
  );
  await page.getByLabel("手工记忆 · 版本 1").fill("项目手工记忆测试：范围更新");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByLabel("手工记忆 · 版本 2")).toHaveValue(
    "项目手工记忆测试：范围更新",
  );
  await page.getByLabel("保存重要信息").fill("第二条已保存记忆");
  await page.getByRole("button", { name: "保存项目记忆", exact: true }).click();
  await expect(page.locator(".memory-item")).toHaveCount(2);
  await page.getByLabel("手工记忆 · 版本 1").fill("第二条尚未保存的草稿");
  await page.getByLabel("手工记忆 · 版本 2").fill("第一条单独保存");
  await page
    .locator(".memory-item")
    .first()
    .getByRole("button", { name: "保存修改", exact: true })
    .click();
  await expect(page.getByLabel("手工记忆 · 版本 3")).toHaveValue(
    "第一条单独保存",
  );
  await expect(page.getByLabel("手工记忆 · 版本 1")).toHaveValue(
    "第二条尚未保存的草稿",
  );
  await page.getByLabel("保存重要信息").fill("新增第三条");
  await page.getByRole("button", { name: "保存项目记忆", exact: true }).click();
  await expect(page.locator(".memory-item")).toHaveCount(3);
  await expect(
    page.locator(".memory-item").nth(1).locator("textarea"),
  ).toHaveValue("第二条尚未保存的草稿");
  await page
    .locator(".memory-item")
    .last()
    .getByRole("button", { name: "删除记忆", exact: true })
    .click();
  await expect(page.locator(".memory-item")).toHaveCount(2);
  await expect(
    page.locator(".memory-item").nth(1).locator("textarea"),
  ).toHaveValue("第二条尚未保存的草稿");
  await page
    .locator(".memory-item")
    .nth(1)
    .getByRole("button", { name: "保存修改", exact: true })
    .click();
  await expect(page.locator(".memory-item").nth(1)).toContainText("版本 2");
  await page.reload();
  await expect(page.getByLabel("手工记忆 · 版本 3")).toHaveValue(
    "第一条单独保存",
  );
  await expect(page.getByLabel("手工记忆 · 版本 2")).toHaveValue(
    "第二条尚未保存的草稿",
  );
  await page.screenshot({
    path: info.outputPath("projects-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 850 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("projects-mobile.png"),
    fullPage: true,
  });
});
