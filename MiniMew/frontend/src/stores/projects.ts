import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { request, ApiError } from "../api";
import { isId } from "../documents";
import { useAuthStore } from "./auth";
import type { components } from "../generated/api";
type Project = components["schemas"]["ProjectView"];
type Memory = components["schemas"]["MemoryView"];
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null;
const invalid = (): never => {
  throw new Error("Invalid project response");
};
function parseProject(v: unknown): Project {
  if (
    !record(v) ||
    !isId(v.id) ||
    typeof v.name !== "string" ||
    !v.name ||
    typeof v.created_at !== "string"
  )
    return invalid();
  return v as Project;
}
function parseMemory(v: unknown): Memory {
  if (
    !record(v) ||
    !isId(v.id) ||
    !isId(v.project_id) ||
    typeof v.content !== "string" ||
    typeof v.enabled !== "boolean" ||
    !Number.isInteger(v.revision) ||
    Number(v.revision) < 1
  )
    return invalid();
  return v as Memory;
}
export const useProjectsStore = defineStore("projects", () => {
  const auth = useAuthStore(),
    items = ref<Project[]>([]),
    selected = ref<Project | null>(null),
    memories = ref<Memory[]>([]),
    error = ref(""),
    busy = ref(false),
    loading = ref(false),
    nextOffset = ref<number | null>(null);
  let epoch = 0,
    selection = 0,
    listOrder = 0;
  const controllers = new Set<AbortController>();
  const context = () => ({ epoch, user: auth.user?.id });
  const valid = (ctx: ReturnType<typeof context>) =>
    ctx.epoch === epoch &&
    !!ctx.user &&
    ctx.user === auth.user?.id &&
    auth.status === "authenticated";
  function reset() {
    epoch++;
    selection++;
    listOrder++;
    controllers.forEach((c) => c.abort());
    controllers.clear();
    items.value = [];
    selected.value = null;
    memories.value = [];
    error.value = "";
    nextOffset.value = null;
    busy.value = loading.value = false;
  }
  watch(() => auth.user?.id, reset, { flush: "sync" });
  async function call(
    ctx: ReturnType<typeof context>,
    path: string,
    method = "GET",
    body?: unknown,
  ) {
    if (!valid(ctx) || !auth.accessToken) throw new Error();
    const controller = new AbortController();
    controllers.add(controller);
    const token = auth.accessToken;
    try {
      try {
        return await request(path, method, body, token, controller.signal);
      } catch (cause) {
        if (
          !(cause instanceof ApiError) ||
          cause.status !== 401 ||
          !valid(ctx) ||
          !(await auth.refreshAccess(token)) ||
          !valid(ctx) ||
          !auth.accessToken
        )
          throw cause;
        const next = auth.accessToken;
        try {
          return await request(path, method, body, next, controller.signal);
        } catch (again) {
          if (again instanceof ApiError && again.status === 401)
            auth.requireRecovery(next);
          throw again;
        }
      }
    } finally {
      controllers.delete(controller);
    }
  }
  const message = (cause: unknown) =>
    cause instanceof ApiError && cause.status === 409
      ? "记忆版本已变更或超过20条启用记忆的预算，请重新加载后调整。"
      : cause instanceof ApiError && cause.status === 404
        ? "项目或记忆不存在，或当前账号没有访问权限。"
        : "服务或连接异常，请刷新确认保存结果，再决定是否重试。";
  async function load(more = false) {
    const ctx = context(),
      order = ++listOrder;
    loading.value = true;
    try {
      const raw = await call(
        ctx,
        `/v1/projects?offset=${more ? (nextOffset.value ?? 0) : 0}`,
      );
      if (!valid(ctx) || order !== listOrder) return;
      if (
        !record(raw) ||
        !Array.isArray(raw.items) ||
        !(raw.next_offset === null || Number.isInteger(raw.next_offset))
      )
        return invalid();
      const page = raw.items.map(parseProject);
      items.value = more ? [...items.value, ...page] : page;
      nextOffset.value = raw.next_offset as number | null;
    } catch (cause) {
      if (valid(ctx) && order === listOrder) error.value = message(cause);
    } finally {
      if (valid(ctx) && order === listOrder) loading.value = false;
    }
  }
  async function select(id?: string) {
    const ctx = context(),
      order = ++selection;
    selected.value = null;
    memories.value = [];
    error.value = "";
    busy.value = false;
    if (!id) return;
    if (!isId(id)) {
      error.value = "项目ID无效。";
      return;
    }
    try {
      const project = parseProject(await call(ctx, `/v1/projects/${id}`));
      const raw = await call(ctx, `/v1/projects/${id}/memories`);
      if (!valid(ctx) || order !== selection) return;
      if (!record(raw) || !Array.isArray(raw.items)) return invalid();
      const rows = raw.items.map(parseMemory);
      if (rows.some((r) => r.project_id !== id) || project.id !== id)
        return invalid();
      selected.value = project;
      memories.value = rows;
    } catch (cause) {
      if (valid(ctx) && order === selection) error.value = message(cause);
    }
  }
  async function create(name: string) {
    const ctx = context(),
      order = selection;
    busy.value = true;
    error.value = "";
    try {
      const item = parseProject(
        await call(ctx, "/v1/projects", "POST", { name }),
      );
      if (!valid(ctx)) return null;
      await load();
      return valid(ctx) && order === selection ? item.id : null;
    } catch (cause) {
      if (valid(ctx)) error.value = message(cause);
      return null;
    } finally {
      if (valid(ctx)) busy.value = false;
    }
  }
  async function save(
    content: string,
    row?: Memory,
    enabled = true,
    remove = false,
  ) {
    const ctx = context(),
      project = selected.value?.id,
      order = selection;
    if (!project || busy.value) return false;
    busy.value = true;
    error.value = "";
    try {
      const raw = await call(
        ctx,
        `/v1/projects/${project}/memories${row ? "/" + row.id : ""}`,
        remove ? "DELETE" : row ? "PATCH" : "POST",
        remove
          ? undefined
          : { content, enabled, ...(row ? { revision: row.revision } : {}) },
      );
      if (!valid(ctx) || order !== selection) return false;
      if (remove && row) {
        memories.value = memories.value.filter((m) => m.id !== row.id);
      } else {
        const saved = parseMemory(raw);
        if (saved.project_id !== project || (row && saved.id !== row.id))
          return invalid();
        memories.value = row
          ? memories.value.map((m) => (m.id === row.id ? saved : m))
          : [...memories.value, saved];
      }
      return true;
    } catch (cause) {
      if (valid(ctx) && order === selection) error.value = message(cause);
      return false;
    } finally {
      if (valid(ctx) && selected.value?.id === project) busy.value = false;
    }
  }
  return {
    items,
    selected,
    memories,
    error,
    busy,
    loading,
    nextOffset,
    reset,
    load,
    select,
    create,
    save,
  };
});
