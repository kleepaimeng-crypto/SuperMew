import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5175",
    browserName: "chromium",
    channel: process.env.MINIMEW_TEST_BROWSER_CHANNEL,
    headless: true,
    // 仅自有CPU模型验收限制Chromium进程/GPU提交额度，不改用户浏览器或应用。
    launchOptions:
      process.env.MINIMEW_PROJECT_BROWSER_TESTS === "1"
        ? { args: ["--renderer-process-limit=2", "--disable-gpu"] }
        : undefined,
  },
  webServer: {
    command: "npm run dev -- --port 5175",
    url: "http://127.0.0.1:5175",
    reuseExistingServer: false,
  },
});
