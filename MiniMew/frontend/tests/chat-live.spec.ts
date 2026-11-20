import { test, expect } from "@playwright/test";

test("真实 API/PG：提问、Journal/SSE、取消、失败与刷新恢复", async ({
  page,
}) => {
  test.skip(
    !process.env.MINIMEW_CHAT_LIVE,
    "Only the owned temporary database runner enables this test",
  );
  const username = `chat_${Date.now()}`;
  await page.goto("/register");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("browser-chat-password-1234");
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await page
    .getByLabel("密码", { exact: true })
    .fill("browser-chat-password-1234");
  const login = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/auth/login",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const token = (await (await login).json()).access_token;
  await page.getByRole("link", { name: "进入我的对话 →", exact: true }).click();
  await page.getByRole("button", { name: "新建对话" }).click();
  await page.getByLabel("你的问题", { exact: true }).fill("普通问题");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  // 过程主线也会显示“已完成”；这里约束Run状态投影仍只出现一次。
  await expect(
    page.getByRole("status").filter({ hasText: /^已完成$/ }),
  ).toHaveCount(1);
  await page.reload();
  await expect(page.getByText("今天天气很好", { exact: true })).toBeVisible();
  await page.getByLabel("你的问题", { exact: true }).fill("请取消本次回复");
  const reserved = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      new URL(r.url()).pathname.endsWith("/runs"),
  );
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  const runId = (await (await reserved).json()).id;
  // 9.3运行时优先显示过程，半截正文暂缓显示；Journal才是已提交分片的事实来源。
  const thread = new URL(page.url()).pathname.split("/").at(-1);
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `/v1/threads/${thread}/runs/${runId}/events?after=0`,
        {
          headers: { Authorization: `Bearer ${token}` },
        },
      );
      expect(response.status()).toBe(200);
      return (await response.json()).items
        .filter((e: { type: string }) => e.type === "message.delta")
        .map((e: { data: { content: string } }) => e.data.content);
    })
    .toContain("今天");
  await expect(
    page.getByRole("status").filter({ hasText: /^生成中$/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "停止回复", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: /^已取消$/ }),
  ).toBeVisible();
  await expect(page.getByText("今天", { exact: true })).toBeVisible();
  await page.getByLabel("你的问题", { exact: true }).fill("模拟失败");
  await page.getByRole("button", { name: "发送问题", exact: true }).click();
  await expect(page.getByText("回答未完成", { exact: true })).toBeVisible();
  await expect(page.getByLabel("本次回答失败原因")).toContainText(
    "模型服务调用失败",
  );
  await page.getByText("错误详情", { exact: true }).click();
  await expect(page.getByLabel("本次回答失败原因")).toContainText(
    "PROVIDER_ERROR",
  );
  await expect(page.getByRole("article", { name: "MiniMew 回复" })).toHaveCount(
    3,
  );
  await page.reload();
  await expect(
    page.getByRole("status").filter({ hasText: /^已取消$/ }),
  ).toBeVisible();
  await expect(page.getByText("回答未完成", { exact: true })).toBeVisible();
  await expect(page.getByLabel("本次回答失败原因")).toContainText(
    "模型服务调用失败",
  );
  await page.screenshot({
    path: "../.artifacts/chat-live.png",
    fullPage: true,
  });
  const storage = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(storage).not.toContain("access_token");
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("article")).toHaveCount(0);
});
