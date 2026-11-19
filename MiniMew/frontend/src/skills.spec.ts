import { it, expect } from "vitest";
import { parseSkill, parseSkills, parseSelection } from "./skills";
import { parseRun } from "./chat";
import { run, ids } from "../tests/fixtures/chat";
const skill = {
  id: "document-qa",
  version: "1.0.0",
  name: "文档问答",
  description: "根据资料回答",
  digest: "a".repeat(64),
};
it("目录与Run保留实际模式，旧历史不补造默认", () => {
  expect(parseSkills([skill])).toEqual([skill]);
  expect(parseRun({ ...run, skill }, ids.thread).skill).toEqual(skill);
  expect(parseRun({ ...run, skill: null }, ids.thread).skill).toBeNull();
  expect(parseRun(run, ids.thread).skill).toBeUndefined();
  expect(() => parseSkills([skill, skill])).toThrow();
});
it("拒绝不可解析的版本、摘要、名称或本地恢复选择", () => {
  for (const patch of [
    { id: "../private" },
    { version: "latest" },
    { digest: "secret" },
    { name: "x".repeat(81) },
  ])
    expect(() => parseSkill({ ...skill, ...patch })).toThrow();
  expect(() => parseSelection({ id: "document-qa", version: 3 })).toThrow();
});
