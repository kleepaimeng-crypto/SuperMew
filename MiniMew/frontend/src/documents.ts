import type { components } from "./generated/api";

export type Document = components["schemas"]["DocumentResponse"];
export type DocumentPage = components["schemas"]["DocumentPage"];
export type IndexJob = components["schemas"]["IndexJobResponse"];
export type Version = components["schemas"]["VersionResponse"];
export type UploadReceipt = components["schemas"]["UploadReceipt"];
export type EmbeddingChange = components["schemas"]["EmbeddingChange"];
export type EmbeddingSettings = components["schemas"]["EmbeddingSettingsView"];
const jobs = [
  "pending",
  "running",
  "staged",
  "succeeded",
  "failed",
  "cancelled",
  "dead_letter",
];
const stages = [
  "reserved",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
  "verifying",
  "publishing",
  "ready",
];
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null;
export const isId = (v: unknown): v is string =>
  typeof v === "string" && /^[a-f\d]{8}(-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(v);
const integer = (v: unknown): v is number =>
  typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const date = (v: unknown) =>
  typeof v === "string" && Number.isFinite(Date.parse(v));
const invalid = (): never => {
  throw new Error("Invalid document response");
};

export function parseEmbeddingSettings(value: unknown): EmbeddingSettings {
  if (
    !record(value) ||
    !["local", "bailian"].includes(String(value.current_source)) ||
    typeof value.current_model !== "string" ||
    !integer(value.dimensions) ||
    value.dimensions === 0 ||
    typeof value.bailian_key_configured !== "boolean"
  )
    return invalid();
  const rebuild = value.rebuild;
  if (
    rebuild !== null &&
    (!record(rebuild) ||
      !isId(rebuild.id) ||
      !["building", "succeeded", "failed"].includes(String(rebuild.status)) ||
      typeof rebuild.target_model !== "string" ||
      !integer(rebuild.completed) ||
      !integer(rebuild.total) ||
      rebuild.completed > rebuild.total ||
      !(rebuild.error_code === null || typeof rebuild.error_code === "string"))
  )
    return invalid();
  return value as EmbeddingSettings;
}

export function parseJob(value: unknown): IndexJob {
  if (
    !record(value) ||
    ![value.id, value.document_id, value.version_id].every(isId) ||
    !jobs.includes(String(value.status)) ||
    !stages.includes(String(value.stage)) ||
    !integer(value.progress_done) ||
    !(value.progress_total === null || integer(value.progress_total)) ||
    (value.progress_total !== null &&
      value.progress_done > (value.progress_total as number)) ||
    !integer(value.attempt) ||
    !date(value.created_at) ||
    !(value.finished_at === null || date(value.finished_at)) ||
    !(
      value.error_code === null ||
      ["INVALID_SOURCE", "SOURCE_UNAVAILABLE", "BUILD_FAILED"].includes(
        String(value.error_code),
      )
    )
  )
    return invalid();
  const terminal = ["succeeded", "failed", "cancelled", "dead_letter"].includes(
    String(value.status),
  );
  if (terminal !== (value.finished_at !== null)) return invalid();
  return value as IndexJob;
}

export function parseDocument(value: unknown): Document {
  if (
    !record(value) ||
    !isId(value.id) ||
    !isId(value.knowledge_base_id) ||
    typeof value.title !== "string" ||
    !value.title ||
    !date(value.created_at) ||
    !(value.current_version_id === null || isId(value.current_version_id)) ||
    !(value.pending_version_id === null || isId(value.pending_version_id))
  )
    return invalid();
  const latest = value.latest_job === null ? null : parseJob(value.latest_job);
  if (
    latest &&
    (latest.document_id !== value.id ||
      (value.pending_version_id !== null &&
        latest.version_id !== value.pending_version_id))
  )
    return invalid();
  return { ...value, latest_job: latest } as Document;
}

export function parsePage(value: unknown): DocumentPage {
  if (
    !record(value) ||
    !Array.isArray(value.items) ||
    !(value.next_offset === null || integer(value.next_offset))
  )
    return invalid();
  return {
    items: value.items.map(parseDocument),
    next_offset: value.next_offset as number | null,
  };
}

export function parseVersion(value: unknown): Version {
  if (
    !record(value) ||
    !isId(value.id) ||
    !isId(value.document_id) ||
    !integer(value.version_number) ||
    value.version_number < 1 ||
    !["candidate", "published", "failed", "superseded"].includes(
      String(value.status),
    ) ||
    typeof value.source_filename !== "string" ||
    !["markdown", "text"].includes(String(value.source_format)) ||
    !integer(value.source_size) ||
    !date(value.created_at) ||
    typeof value.content_sha256 !== "string" ||
    !/^[a-f\d]{64}$/.test(value.content_sha256) ||
    typeof value.build_fingerprint !== "string" ||
    !/^[a-f\d]{64}$/.test(value.build_fingerprint)
  )
    return invalid();
  const build = value.build_configuration;
  if (
    !record(build) ||
    !record(build.embedding) ||
    !["bge-small-zh-v1.5", "bge-m3"].includes(String(build.embedding.id)) ||
    !integer(build.embedding.dimension) ||
    typeof build.embedding.revision !== "string" ||
    !/^[a-f\d]{40}$/.test(build.embedding.revision)
  )
    return invalid();
  return value as Version;
}

export function parseReceipt(value: unknown): UploadReceipt {
  if (!record(value)) return invalid();
  const document = parseDocument(value.document),
    version = parseVersion(value.version),
    job = parseJob(value.job);
  if (
    version.document_id !== document.id ||
    job.document_id !== document.id ||
    job.version_id !== version.id
  )
    return invalid();
  return { document, version, job };
}

export const activeJob = (job: IndexJob | null | undefined) =>
  !!job && ["pending", "running", "staged"].includes(job.status);
export function jobLabel(job: IndexJob | null | undefined) {
  if (!job) return "暂无构建任务";
  if (job.status === "pending") return "等待处理";
  if (job.status === "staged") return "待发布";
  if (job.status === "succeeded") return "构建成功";
  if (job.status === "failed") return "构建失败";
  if (job.status === "dead_letter") return "重试已耗尽";
  if (job.status === "cancelled") return "已取消";
  return {
    reserved: "准备处理",
    parsing: "解析文档",
    chunking: "整理片段",
    embedding: "向量编码",
    indexing: "写入索引",
    verifying: "核验索引",
    publishing: "发布版本",
    ready: "已核验，等待整库发布",
  }[job.stage];
}

export function jobError(job: IndexJob | null | undefined) {
  if (!job || !["failed", "dead_letter"].includes(job.status)) return "";
  return {
    INVALID_SOURCE:
      "文档内容无法处理，可能包含超过分块预算的代码或表格。请调整文件后上传新版。",
    SOURCE_UNAVAILABLE: "原文件无法读取或校验。请检查文件存储，或重新上传。",
    BUILD_FAILED: "索引构建失败。请检查索引服务后，重新上传。",
  }[job.error_code ?? "BUILD_FAILED"];
}
