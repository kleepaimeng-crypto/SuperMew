import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

const proxy = {
  target: process.env.MINIMEW_API_TARGET ?? "http://127.0.0.1:8001",
  // 保留浏览器 Host，与 Origin 一致，不绕开后端同源校验。
  changeOrigin: false,
};

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: { "/auth": proxy, "/v1": proxy },
  },
  test: { environment: "node", include: ["src/**/*.spec.ts"] },
});
