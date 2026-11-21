import type { components } from "./generated/api";
import { isThreadId } from "./threads";
export type Template = components["schemas"]["TemplateView"];
export type ProjectModels = components["schemas"]["ProjectModelsView"];
export const endpoints = {
  embedding: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  rerank: "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
};
export const models = {
  embedding: "text-embedding-v4",
  rerank: "qwen3-rerank",
};
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null;
export function parseTemplates(value: unknown): Template[] {
  if (
    !Array.isArray(value) ||
    value.some(
      (v) =>
        !record(v) ||
        typeof v.id !== "string" ||
        !isThreadId(v.id) ||
        typeof v.name !== "string" ||
        !["embedding", "rerank"].includes(String(v.purpose)) ||
        v.provider !== "bailian" ||
        typeof v.model_name !== "string" ||
        typeof v.base_url !== "string" ||
        typeof v.key_configured !== "boolean",
    )
  )
    throw new Error("Invalid retrieval configurations");
  return value as Template[];
}
export function parseProjectModels(value: unknown): ProjectModels {
  if (!record(value)) throw new Error("Invalid project model configuration");
  for (const purpose of ["embedding", "rerank"]) {
    const v = value[purpose];
    if (
      !record(v) ||
      !["local", "bailian"].includes(String(v.source)) ||
      typeof v.model_name !== "string" ||
      (v.base_url !== null && typeof v.base_url !== "string") ||
      typeof v.key_configured !== "boolean"
    )
      throw new Error("Invalid project model configuration");
  }
  return value as ProjectModels;
}
