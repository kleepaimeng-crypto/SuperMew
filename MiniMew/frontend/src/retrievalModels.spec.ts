import { expect, it } from "vitest";
import { parseProjectModels, parseTemplates } from "./retrievalModels";
it("拒绝损坏的模型配置和项目绑定", () => {
  expect(() => parseTemplates({})).toThrow();
  expect(() => parseTemplates([{ id: "not-a-uuid" }])).toThrow();
  expect(() =>
    parseProjectModels({ embedding: { source: "other" } }),
  ).toThrow();
});
it("读取云端模板与独立项目只读选择", () => {
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
  expect(
    parseProjectModels({ embedding: cloud, rerank: local }).embedding,
  ).toEqual(cloud);
  expect(
    parseTemplates([
      {
        id: "11111111-1111-4111-8111-111111111111",
        name: "百炼",
        purpose: "embedding",
        provider: "bailian",
        model_name: cloud.model_name,
        base_url: cloud.base_url,
        key_configured: true,
      },
    ]),
  ).toHaveLength(1);
});
