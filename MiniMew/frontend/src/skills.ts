import type { components } from "./generated/api";
import { isRecord } from "./events/runEventContract";

export type SkillSummary = components["schemas"]["SkillSummary"];
export type SkillSelection = components["schemas"]["SkillSelection"];
export function parseSkill(value: unknown): SkillSummary {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !/^[a-z][a-z0-9-]{0,63}$/.test(value.id) ||
    typeof value.version !== "string" ||
    !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(value.version) ||
    typeof value.name !== "string" ||
    !value.name ||
    [...value.name].length > 80 ||
    typeof value.description !== "string" ||
    !value.description ||
    [...value.description].length > 240 ||
    typeof value.digest !== "string" ||
    !/^[a-f0-9]{64}$/.test(value.digest)
  )
    throw new Error("Invalid Skill summary");
  return {
    id: value.id,
    version: value.version,
    name: value.name,
    description: value.description,
    digest: value.digest,
  };
}
export function parseSkills(value: unknown): SkillSummary[] {
  if (!Array.isArray(value) || value.length > 16)
    throw new Error("Invalid Skill catalog");
  const items = value.map(parseSkill);
  if (new Set(items.map((s) => `${s.id}@${s.version}`)).size !== items.length)
    throw new Error("Duplicate Skill release");
  return items;
}
export function parseSelection(value: unknown): SkillSelection {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !/^[a-z][a-z0-9-]{0,63}$/.test(value.id) ||
    typeof value.version !== "string" ||
    !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(value.version)
  )
    throw new Error("Invalid Skill choice");
  return { id: value.id, version: value.version };
}
