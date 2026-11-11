import { expect, it } from "vitest";
import { fixture, id, time } from "./document-fixtures";
import {
  activeJob,
  jobLabel,
  parseDocument,
  parseJob,
  parsePage,
  parseReceipt,
  parseVersion,
} from "./documents";

it("候选/staged与已发布指针分开表达", () => {
  const { document, job } = fixture();
  expect(parseDocument(document).current_version_id).toBeNull();
  expect(activeJob({ ...job, status: "staged", stage: "publishing" })).toBe(
    true,
  );
  expect(jobLabel({ ...job, status: "staged" })).toBe("待发布");
});
it("失败的最近Job与仍可用旧版能够同时恢复", () => {
  const { document, job } = fixture();
  const value = parseDocument({
    ...document,
    current_version_id: id(99),
    pending_version_id: null,
    latest_job: {
      ...job,
      status: "failed",
      finished_at: time,
      error_code: "BUILD_FAILED",
    },
  });
  expect(value.current_version_id).toBe(id(99));
  expect(value.latest_job?.status).toBe("failed");
});
it.each([
  { progress_done: 3, progress_total: 2 },
  { progress_done: -1 },
  { status: "succeeded", finished_at: null },
  { error_code: "private-secret" },
  { version_id: "other-version" },
])("拒绝畸形任务响应 %j", (change) => {
  expect(() => parseJob({ ...fixture().job, ...change })).toThrow();
});
it("拒绝跨文档任务与错误上传回执", () => {
  const a = fixture(),
    b = fixture(5);
  expect(() => parseDocument({ ...a.document, latest_job: b.job })).toThrow();
  expect(() => parseReceipt({ ...a, job: b.job })).toThrow();
});
it("校验分页与版本身份，不能把坏响应当空目录", () => {
  expect(() => parsePage({ items: {}, next_offset: null })).toThrow();
  expect(() => parsePage({ items: [], next_offset: -1 })).toThrow();
  expect(() =>
    parseVersion({ ...fixture().version, content_sha256: "bad" }),
  ).toThrow();
  expect(parseReceipt(fixture()).job.id).toBe(fixture().job.id);
});
