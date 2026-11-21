import type { components } from "./generated/api";

export type Thread = components["schemas"]["ThreadResponse"];
export type ThreadPage = components["schemas"]["ThreadListResponse"];

export function isThreadId(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}

export function parseThread(value: unknown): Thread {
  if (
    typeof value !== "object" ||
    value === null ||
    !("id" in value) ||
    typeof value.id !== "string" ||
    !isThreadId(value.id) ||
    !("title" in value) ||
    typeof value.title !== "string" ||
    value.title.length > 200 ||
    !("created_at" in value) ||
    typeof value.created_at !== "string" ||
    Number.isNaN(Date.parse(value.created_at))
  ) {
    throw new Error("Invalid thread response");
  }
  if ("archived" in value && typeof value.archived !== "boolean")
    throw new Error("Invalid archive status");
  if (
    "project_id" in value &&
    value.project_id !== null &&
    (typeof value.project_id !== "string" || !isThreadId(value.project_id))
  )
    throw new Error("Invalid project ID");
  return {
    id: value.id,
    title: value.title,
    created_at: value.created_at,
    archived: "archived" in value ? (value.archived as boolean) : false,
    ...("project_id" in value
      ? { project_id: value.project_id as string | null }
      : {}),
  };
}

export function parseThreadPage(value: unknown, offset: number): ThreadPage {
  if (
    typeof value !== "object" ||
    value === null ||
    !("items" in value) ||
    !Array.isArray(value.items) ||
    value.items.length > 20 ||
    !("next_offset" in value) ||
    !(
      value.next_offset === null ||
      (typeof value.next_offset === "number" &&
        Number.isSafeInteger(value.next_offset) &&
        value.next_offset > offset)
    )
  ) {
    throw new Error("Invalid thread page");
  }
  return {
    items: value.items.map(parseThread),
    next_offset: value.next_offset,
  };
}
