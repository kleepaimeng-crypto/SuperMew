import { expect, it } from "vitest";
import { answerFixture } from "../tests/fixtures/answer";
import { answerBlocks, parseAnswer, supplementHeading } from "./answers";

it("引用位置按Unicode码点处理，表情不会使引用错位", () => {
  const { message, response } = answerFixture();
  const materials = parseAnswer(response, message)!;
  const blocks = answerBlocks(message.content, materials);
  expect(blocks[0]!.parts.filter((p) => p.citationId)).toEqual([
    { text: "[E1]", citationId: "E1" },
  ]);
  expect(blocks[0]!.parts[0]!.text).toContain("🍚餐费上限150元");
  expect(blocks[1]!.kind).toBe("supplement");
});

it.each([
  "run",
  "message",
  "offset",
  "source",
  "duplicate",
  "unknown",
  "extra",
])("拒绝无效引用资料：%s", (mode) => {
  const { message, response } = answerFixture();
  if (mode === "run") response.run_id = "wrong";
  if (mode === "message") response.message_id = "wrong";
  if (mode === "offset") ++response.materials.citations[0]!.start;
  if (mode === "source")
    response.materials.evidence[0]!.version_id = "not-a-uuid";
  if (mode === "duplicate")
    response.materials.evidence.push(response.materials.evidence[0]!);
  if (mode === "unknown") response.materials.evidence[0]!.citation_id = "E99";
  if (mode === "extra")
    Object.assign(response.materials, { raw_secret: "not allowed" });
  expect(() => parseAnswer(response, message)).toThrow();
});

it("旧消息无引用与健康空知识补充有不同形状", () => {
  const { message, response } = answerFixture();
  expect(parseAnswer({ ...response, materials: null }, message)).toBeNull();
  message.content = supplementHeading + "\n尚无资料，以下是一般建议。";
  const materials = {
    sections: [
      {
        kind: "supplement",
        start: [...supplementHeading].length,
        end: [...message.content].length,
      },
    ],
    citations: [],
    evidence: [],
  };
  expect(parseAnswer({ ...response, materials }, message)).toEqual(materials);
});
