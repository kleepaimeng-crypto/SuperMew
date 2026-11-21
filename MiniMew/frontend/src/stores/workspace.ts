import { defineStore } from "pinia";
import { reactive, ref, watch } from "vue";
import { ApiError, request } from "../api";
import { parseThread, parseThreadPage, type Thread } from "../threads";
import { useAuthStore } from "./auth";
import { useProjectsStore } from "./projects";

export const PERSONAL_SCOPE = "personal";

export interface ScopeThreads {
  items: Thread[];
  nextOffset: number | null;
  loaded: boolean;
  loading: boolean;
  error: string;
}

interface ScopeMeta {
  version: number;
  controller?: AbortController;
}

function emptyBucket(): ScopeThreads {
  return {
    items: [],
    nextOffset: null,
    loaded: false,
    loading: false,
    error: "",
  };
}

export function scopeKeyFor(projectId: string | null | undefined): string {
  return projectId ? projectId : PERSONAL_SCOPE;
}

export const useWorkspaceStore = defineStore("workspace", () => {
  const auth = useAuthStore();
  const projects = useProjectsStore();

  const activeScope = ref<string | null>(null);
  const expandedScopes = reactive<Record<string, boolean>>({
    [PERSONAL_SCOPE]: true,
  });
  const buckets = reactive<Record<string, ScopeThreads>>({
    [PERSONAL_SCOPE]: emptyBucket(),
  });
  const mobileNavOpen = ref(false);
  const archiveBusy = ref(false),
    archiveError = ref("");

  let epoch = 0;
  const meta = new Map<string, ScopeMeta>();

  function ensureBucket(key: string): ScopeThreads {
    if (!buckets[key]) buckets[key] = emptyBucket();
    if (!meta.has(key)) meta.set(key, { version: 0 });
    return buckets[key]!;
  }

  function reset() {
    ++epoch;
    meta.forEach((entry) => {
      entry.version++;
      entry.controller?.abort();
      entry.controller = undefined;
    });
    meta.clear();
    Object.keys(buckets).forEach((key) => delete buckets[key]);
    buckets[PERSONAL_SCOPE] = emptyBucket();
    Object.keys(expandedScopes).forEach((key) => delete expandedScopes[key]);
    expandedScopes[PERSONAL_SCOPE] = true;
    activeScope.value = null;
    mobileNavOpen.value = false;
    archiveBusy.value = false;
    archiveError.value = "";
  }

  watch(() => auth.user?.id, reset, { flush: "sync" });

  function context() {
    if (auth.status !== "authenticated" || !auth.accessToken || !auth.user)
      return null;
    return { epoch, token: auth.accessToken, userId: auth.user.id };
  }

  function current(ctx: NonNullable<ReturnType<typeof context>>) {
    return ctx.epoch === epoch && auth.user?.id === ctx.userId;
  }

  async function authorizedGet(
    ctx: NonNullable<ReturnType<typeof context>>,
    path: string,
    signal: AbortSignal,
    method = "GET",
    body?: unknown,
  ) {
    try {
      return await request(path, method, body, ctx.token, signal);
    } catch (cause) {
      if (
        !(cause instanceof ApiError) ||
        cause.status !== 401 ||
        !current(ctx) ||
        !(await auth.refreshAccess(ctx.token)) ||
        !current(ctx) ||
        !auth.accessToken
      )
        throw cause;
      const nextToken = auth.accessToken;
      try {
        return await request(path, method, body, nextToken, signal);
      } catch (retryFailure) {
        if (retryFailure instanceof ApiError && retryFailure.status === 401)
          auth.requireRecovery(nextToken);
        throw retryFailure;
      }
    }
  }

  async function loadScopeThreads(
    projectId: string | null,
    restart = false,
    archived = false,
  ): Promise<void> {
    const ctx = context();
    const key = scopeKeyFor(projectId) + (archived ? ":archived" : "");
    const bucket = ensureBucket(key);
    const entry = meta.get(key)!;
    if (!ctx || (!restart && bucket.loading)) return;
    if (!restart && bucket.loaded && bucket.nextOffset === null) return;

    const offset = restart || !bucket.loaded ? 0 : bucket.nextOffset!;
    if (restart) {
      bucket.loaded = false;
      bucket.nextOffset = null;
    }
    const version = ++entry.version;
    entry.controller?.abort();
    const controller = (entry.controller = new AbortController());
    bucket.loading = true;
    bucket.error = "";

    try {
      const page = parseThreadPage(
        await authorizedGet(
          ctx,
          `/v1/threads?limit=20&offset=${offset}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}${archived ? "&archived=true" : ""}`,
          controller.signal,
        ),
        offset,
      );
      if (!current(ctx) || entry.version !== version) return;
      const combined =
        offset === 0 ? page.items : [...bucket.items, ...page.items];
      bucket.items = [
        ...new Map(combined.map((item) => [item.id, item])).values(),
      ];
      bucket.nextOffset = page.next_offset;
      bucket.loaded = true;
    } catch (cause) {
      if (
        !current(ctx) ||
        entry.version !== version ||
        controller.signal.aborted
      )
        return;
      if (cause instanceof ApiError && cause.status === 401) {
        auth.requireRecovery(ctx.token);
        return;
      }
      bucket.error = "对话列表加载失败，请重试。";
    } finally {
      if (current(ctx) && entry.version === version) bucket.loading = false;
    }
  }

  function setActiveScope(projectId: string | null, expand = true) {
    activeScope.value = projectId;
    const key = scopeKeyFor(projectId);
    const bucket = ensureBucket(key);
    if (expand) expandedScopes[key] = true;
    if (!bucket.loaded && !bucket.loading) {
      void loadScopeThreads(projectId, true);
    }
  }

  function toggleScope(projectId: string | null) {
    const key = scopeKeyFor(projectId);
    const next = !expandedScopes[key];
    expandedScopes[key] = next;
    activeScope.value = projectId;
    const bucket = ensureBucket(key);
    if (next && !bucket.loaded && !bucket.loading) {
      void loadScopeThreads(projectId, true);
    }
  }

  function upsertThread(thread: Thread) {
    const projectId = thread.project_id ?? null;
    const key = scopeKeyFor(projectId);
    const bucket = ensureBucket(key);
    if (thread.archived) {
      bucket.items = bucket.items.filter((item) => item.id !== thread.id);
      return;
    }
    bucket.items = [
      thread,
      ...bucket.items.filter((item) => item.id !== thread.id),
    ];
    expandedScopes[key] = true;
  }

  async function archiveThread(thread: Thread, archived: boolean) {
    const ctx = context();
    if (!ctx || archiveBusy.value) return false;
    archiveBusy.value = true;
    archiveError.value = "";
    try {
      const saved = parseThread(
        await authorizedGet(
          ctx,
          `/v1/threads/${thread.id}`,
          new AbortController().signal,
          "PATCH",
          { archived },
        ),
      );
      if (!current(ctx)) return false;
      if (saved.id !== thread.id || saved.archived !== archived)
        throw new Error("Invalid archive response");
      const scope = saved.project_id ?? null;
      await Promise.all([
        loadScopeThreads(scope, true),
        loadScopeThreads(scope, true, true),
      ]);
      return current(ctx);
    } catch {
      if (current(ctx))
        archiveError.value = "操作结果未确认，请刷新列表后重试。";
      return false;
    } finally {
      if (current(ctx)) archiveBusy.value = false;
    }
  }

  function syncProjectsIfNeeded() {
    if (
      auth.status === "authenticated" &&
      !projects.loading &&
      !projects.items.length &&
      !projects.error
    ) {
      void projects.load();
    }
  }

  function projectName(projectId: string | null): string {
    if (!projectId) return "个人空间";
    const found =
      projects.items.find((p) => p.id === projectId) ??
      (projects.selected?.id === projectId ? projects.selected : null);
    return found?.name ?? "项目空间";
  }

  function openMobileNav() {
    mobileNavOpen.value = true;
  }

  function closeMobileNav() {
    mobileNavOpen.value = false;
  }

  return {
    archiveBusy,
    archiveError,
    archiveThread,
    activeScope,
    expandedScopes,
    buckets,
    mobileNavOpen,
    ensureBucket,
    loadScopeThreads,
    setActiveScope,
    toggleScope,
    upsertThread,
    syncProjectsIfNeeded,
    projectName,
    openMobileNav,
    closeMobileNav,
    reset,
  };
});
