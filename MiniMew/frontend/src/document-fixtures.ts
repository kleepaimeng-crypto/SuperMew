// 测试夹具；业务模块不导入。
import type { Document, IndexJob, Version } from "./documents";
export const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
export const time = "2026-11-11T00:00:00Z";
export function fixture(n = 1) {
  const job: IndexJob = {
    id: id(n + 2),
    document_id: id(n),
    version_id: id(n + 1),
    status: "pending",
    stage: "reserved",
    attempt: 0,
    progress_done: 0,
    progress_total: null,
    error_code: null,
    created_at: time,
    finished_at: null,
  };
  const document: Document = {
    id: id(n),
    knowledge_base_id: id(100),
    title: `制度${n}.md`,
    current_version_id: null,
    pending_version_id: id(n + 1),
    created_at: time,
    latest_job: job,
  };
  const version: Version = {
    id: id(n + 1),
    document_id: id(n),
    version_number: 1,
    status: "candidate",
    source_filename: document.title,
    source_format: "markdown",
    source_size: 100,
    content_sha256: "a".repeat(64),
    build_fingerprint: "b".repeat(64),
    created_at: time,
    build_configuration: {
      schema_version: 1,
      source_format: "markdown",
      parser: "markdown-txt-v1",
      chunker: "structure-v1",
      index_layout: "dense-cosine-v1",
      chunk_tokens: 384,
      overlap_tokens: 48,
      embedding: {
        id: "bge-small-zh-v1.5",
        model_id: "BAAI/bge-small-zh-v1.5",
        revision: "7".repeat(40),
        dimension: 512,
        normalize: true,
        pooling: "cls",
        query_prefix: "为这个句子生成表示以用于检索相关文章：",
        document_prefix: "",
        max_input_tokens: 512,
      },
    },
  };
  return { document, job, version };
}
