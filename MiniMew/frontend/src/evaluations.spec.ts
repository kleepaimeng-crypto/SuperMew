import { expect, it } from "vitest";
import { job, detail, artifact, corpusPage } from "./evaluations";
it("拒绝畸形成功响应，EOF或未知状态不当作完成", () => {
  expect(() =>
    job({ job_id: "bad", status: "completed", cases: [] }),
  ).toThrow();
  expect(() => artifact({ schema_version: 1 })).toThrow();
  expect(() => detail({ case_id: "meal", answer: "answer" })).toThrow();
  expect(() =>
    corpusPage({ items: [{ corpus_id: "arbitrary" }], next_offset: null }),
  ).toThrow();
});
it("允许明确的failed状态，不丢掉持久错误", () => {
  const value = job({
    job_id: "00000000-0000-0000-0000-000000000001",
    status: "failed",
    cancel_requested: false,
    error_code: "EVALUATION_WORKER_FAILED",
    cases: [],
  });
  expect(value.status).toBe("failed");
  expect(value.error_code).toBe("EVALUATION_WORKER_FAILED");
});
