import type { components } from "./generated/api";
import type { ChatMessage } from "./chat";
import { isRecord, isUuid, isEventValue } from "./events/runEventContract";

export type AnswerMaterials = components["schemas"]["AnswerMaterials"];
export type Evidence = components["schemas"]["Evidence"];
export interface AnswerView {
  messageId: string;
  content: string;
  state: "loading" | "loaded" | "error";
  materials: AnswerMaterials | null;
  error: string;
  withdrawnDocIds?: string[];
}
export class AnswerProtocolError extends Error {}
export const documentHeading = "## 文档依据";
export const supplementHeading = "## 模型补充（未由本次资料验证）";
const keys = (v: Record<string, unknown>, wanted: string[]) =>
  Object.keys(v).length === wanted.length &&
  wanted.every((k) => Object.hasOwn(v, k));
const integer = (v: unknown, min = 0): v is number =>
  Number.isSafeInteger(v) && Number(v) >= min;
const text = (v: unknown, max: number): v is string =>
  typeof v === "string" &&
  v.trim().length > 0 &&
  !v.includes("\0") &&
  [...v].length <= max;
const refId = (v: unknown): v is string =>
  typeof v === "string" && /^E[1-9][0-9]*$/.test(v);

export function parseAnswer(
  value: unknown,
  message: ChatMessage,
): AnswerMaterials | null {
  const fail = () => {
    throw new AnswerProtocolError("Invalid answer materials");
  };
  if (
    !isRecord(value) ||
    !(
      keys(value, ["run_id", "message_id", "status", "materials"]) ||
      (keys(value, [
        "run_id",
        "message_id",
        "status",
        "materials",
        "withdrawn_document_ids",
      ]) &&
        Array.isArray(value.withdrawn_document_ids) &&
        value.withdrawn_document_ids.every(isUuid))
    ) ||
    value.run_id !== message.run_id ||
    value.message_id !== message.id ||
    !isEventValue(value.status, "RunStatus") ||
    value.status !== "succeeded" ||
    message.role !== "assistant" ||
    message.status !== "completed"
  )
    return fail();
  if (value.materials === null) return null;
  const m = value.materials;
  if (
    !isRecord(m) ||
    !keys(m, ["sections", "citations", "evidence"]) ||
    !Array.isArray(m.sections) ||
    !Array.isArray(m.citations) ||
    !Array.isArray(m.evidence) ||
    !m.sections.length ||
    m.sections.length > 2 ||
    m.citations.length > 200 ||
    m.evidence.length > 18 ||
    JSON.stringify(m).length > 262144
  )
    return fail();
  const sourceKeys = [
    "knowledge_base_id",
    "document_id",
    "version_id",
    "chunk_id",
    "section_id",
    "title_path",
    "start",
    "end",
    "start_line",
    "end_line",
    "content_sha256",
    "text",
    "citation_id",
  ];
  const known = new Set<string>();
  for (const e of m.evidence) {
    if (
      !isRecord(e) ||
      !keys(e, sourceKeys) ||
      !refId(e.citation_id) ||
      known.has(e.citation_id) ||
      ![
        e.knowledge_base_id,
        e.document_id,
        e.version_id,
        e.chunk_id,
        e.section_id,
      ].every(isUuid) ||
      !Array.isArray(e.title_path) ||
      !e.title_path.every((v) => text(v, 2000)) ||
      !text(e.text, 10000) ||
      !integer(e.start) ||
      !integer(e.end, e.start + 1) ||
      e.end - e.start !== [...e.text].length ||
      !integer(e.start_line, 1) ||
      !integer(e.end_line, e.start_line) ||
      typeof e.content_sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(e.content_sha256)
    )
      return fail();
    known.add(e.citation_id);
  }
  const chars = [...message.content];
  const used = new Set<string>();
  const expected: { citation_id: string; start: number; end: number }[] = [];
  let previousEnd = 0;
  let previousKind = "";
  for (const section of m.sections) {
    if (
      !isRecord(section) ||
      !keys(section, ["kind", "start", "end"]) ||
      !["document", "supplement"].includes(String(section.kind)) ||
      !integer(section.start) ||
      !integer(section.end, section.start + 1) ||
      section.end > chars.length ||
      section.start < previousEnd ||
      previousKind === "supplement" ||
      previousKind === section.kind
    )
      return fail();
    const heading =
      section.kind === "document" ? documentHeading : supplementHeading;
    if (chars.slice(previousEnd, section.start).join("").trim() !== heading)
      return fail();
    const body = chars.slice(section.start, section.end).join("");
    if (!body.trim()) return fail();
    const refs = [...body.matchAll(/\[E[1-9][0-9]*\]/g)];
    if (
      body.replace(/\[E[1-9][0-9]*\]/g, "").includes("[E") ||
      (section.kind === "supplement" && refs.length)
    )
      return fail();
    if (section.kind === "document") {
      if (
        body
          .trim()
          .split(/\n\s*\n/)
          .some((p) => !/\[E[1-9][0-9]*\]/.test(p))
      )
        return fail();
      for (const ref of refs) {
        const id = ref[0].slice(1, -1);
        if (!known.has(id)) return fail();
        const start = section.start + [...body.slice(0, ref.index)].length;
        expected.push({
          citation_id: id,
          start,
          end: start + [...ref[0]].length,
        });
        used.add(id);
      }
    }
    previousEnd = section.end;
    previousKind = String(section.kind);
  }
  if (
    previousEnd !== chars.length ||
    used.size !== known.size ||
    expected.length !== m.citations.length
  )
    return fail();
  for (let i = 0; i < expected.length; ++i) {
    const got = m.citations[i],
      want = expected[i]!;
    if (
      !isRecord(got) ||
      !keys(got, ["citation_id", "start", "end"]) ||
      got.citation_id !== want.citation_id ||
      got.start !== want.start ||
      got.end !== want.end
    )
      return fail();
  }
  return m as unknown as AnswerMaterials;
}

export interface AnswerPart {
  text: string;
  citationId?: string;
}
export function answerBlocks(content: string, materials: AnswerMaterials) {
  const chars = [...content];
  return materials.sections.map((s) => {
    const parts: AnswerPart[] = [];
    let cursor = s.start;
    for (const citation of materials.citations.filter(
      (c) => c.start >= s.start && c.end <= s.end,
    )) {
      parts.push({ text: chars.slice(cursor, citation.start).join("") });
      parts.push({
        text: `[${citation.citation_id}]`,
        citationId: citation.citation_id,
      });
      cursor = citation.end;
    }
    parts.push({ text: chars.slice(cursor, s.end).join("") });
    // 仅收起分区边界换行；引用位置始终按原Message码点计算。
    if (parts[0]) parts[0].text = parts[0].text.replace(/^(?:\r?\n)+/, "");
    const last = parts.at(-1);
    if (last) last.text = last.text.replace(/(?:\r?\n)+$/, "");
    return { kind: s.kind, parts };
  });
}
