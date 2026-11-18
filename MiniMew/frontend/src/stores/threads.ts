import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { ApiError, request } from "../api";
import {
  isThreadId,
  parseThread,
  parseThreadPage,
  type Thread,
} from "../threads";
import { useAuthStore } from "./auth";

export const useThreadsStore = defineStore("threads", () => {
  const auth = useAuthStore();
  const projectId = ref<string | null>(null);
  const items = ref<Thread[]>([]);
  const selected = ref<Thread | null>(null);
  const nextOffset = ref<number | null>(null);
  const loaded = ref(false);
  const listLoading = ref(false);
  const detailLoading = ref(false);
  const creating = ref(false);
  const listError = ref("");
  const detailError = ref("");
  const createError = ref("");
  const unavailable = ref(false);
  let epoch = 0;
  let listVersion = 0;
  let detailVersion = 0;
  let listController: AbortController | undefined;
  let detailController: AbortController | undefined;
  let createController: AbortController | undefined;

  function clearSelection() {
    ++detailVersion;
    detailController?.abort();
    selected.value = null;
    detailLoading.value = false;
    detailError.value = "";
    unavailable.value = false;
  }

  function reset() {
    ++epoch;
    ++listVersion;
    listController?.abort();
    createController?.abort();
    clearSelection();
    items.value = [];
    nextOffset.value = null;
    loaded.value = false;
    listLoading.value = false;
    creating.value = false;
    listError.value = "";
    createError.value = "";
  }
  watch(
    () => auth.user?.id,
    () => {
      projectId.value = null;
      reset();
    },
    { flush: "sync" },
  );

  function context() {
    if (auth.status !== "authenticated" || !auth.accessToken || !auth.user)
      return null;
    return { epoch, token: auth.accessToken, userId: auth.user.id };
  }
  function current(ctx: NonNullable<ReturnType<typeof context>>) {
    return ctx.epoch === epoch && auth.user?.id === ctx.userId;
  }
  async function threadRequest(
    ctx: NonNullable<ReturnType<typeof context>>,
    path: string,
    method: string,
    body: unknown,
    signal: AbortSignal,
  ) {
    try {
      return await request(path, method, body, ctx.token, signal);
    } catch (cause) {
      // 聊天连接可能已为同一用户完成续期，旧 GET/POST 的明确 401 用新凭据再试一次。
      if (
        !(cause instanceof ApiError) ||
        cause.status !== 401 ||
        !current(ctx) ||
        !auth.accessToken ||
        auth.accessToken === ctx.token
      )
        throw cause;
      const token = auth.accessToken;
      try {
        return await request(path, method, body, token, signal);
      } catch (retryFailure) {
        if (retryFailure instanceof ApiError && retryFailure.status === 401)
          auth.requireRecovery(token);
        throw retryFailure;
      }
    }
  }
  function recover(cause: unknown, token: string) {
    if (cause instanceof ApiError && cause.status === 401) {
      auth.requireRecovery(token);
      return true;
    }
    return false;
  }

  async function loadList(restart = false) {
    const ctx = context();
    if (!ctx || (!restart && listLoading.value)) return;
    if (!restart && loaded.value && nextOffset.value === null) return;
    const offset = restart || !loaded.value ? 0 : nextOffset.value!;
    if (restart) {
      loaded.value = false;
      nextOffset.value = null;
    }
    const version = ++listVersion;
    listController?.abort();
    const controller = (listController = new AbortController());
    listLoading.value = true;
    listError.value = "";
    try {
      const page = parseThreadPage(
        await threadRequest(
          ctx,
          `/v1/threads?limit=20&offset=${offset}${projectId.value ? "&project_id=" + projectId.value : ""}`,
          "GET",
          undefined,
          controller.signal,
        ),
        offset,
      );
      if (!current(ctx) || version !== listVersion) return;
      const combined =
        offset === 0 ? page.items : [...items.value, ...page.items];
      items.value = [
        ...new Map(combined.map((item) => [item.id, item])).values(),
      ];
      nextOffset.value = page.next_offset;
      loaded.value = true;
    } catch (cause) {
      if (!current(ctx) || version !== listVersion || controller.signal.aborted)
        return;
      if (!recover(cause, ctx.token))
        listError.value = "对话列表加载失败，请重试。";
    } finally {
      if (current(ctx) && version === listVersion) listLoading.value = false;
    }
  }

  async function selectThread(id: string) {
    clearSelection();
    const ctx = context();
    if (!ctx) return;
    if (!isThreadId(id)) {
      unavailable.value = true;
      return;
    }
    const version = detailVersion;
    const controller = (detailController = new AbortController());
    detailLoading.value = true;
    try {
      const thread = parseThread(
        await threadRequest(
          ctx,
          `/v1/threads/${encodeURIComponent(id)}`,
          "GET",
          undefined,
          controller.signal,
        ),
      );
      if ((thread.project_id ?? null) !== projectId.value)
        throw new ApiError(404);
      if (thread.id.toLowerCase() !== id.toLowerCase())
        throw new Error("Mismatched thread");
      if (current(ctx) && version === detailVersion) selected.value = thread;
    } catch (cause) {
      if (
        !current(ctx) ||
        version !== detailVersion ||
        controller.signal.aborted
      )
        return;
      if (cause instanceof ApiError && [403, 404, 422].includes(cause.status))
        unavailable.value = true;
      else if (!recover(cause, ctx.token))
        detailError.value = "对话加载失败，请重试。";
    } finally {
      if (current(ctx) && version === detailVersion)
        detailLoading.value = false;
    }
  }

  async function createThread(): Promise<string | null> {
    const ctx = context();
    if (!ctx || creating.value) return null;
    const selectionAtStart = detailVersion;
    const controller = (createController = new AbortController());
    creating.value = true;
    createError.value = "";
    try {
      const thread = parseThread(
        await threadRequest(
          ctx,
          "/v1/threads",
          "POST",
          projectId.value ? { project_id: projectId.value } : {},
          controller.signal,
        ),
      );
      if (!current(ctx)) return null;
      // 只有服务端确认后才显示；重载第一页使 offset 与最新列表重新对齐。
      ++listVersion;
      listController?.abort();
      items.value = [
        thread,
        ...items.value.filter((item) => item.id !== thread.id),
      ];
      loaded.value = false;
      nextOffset.value = null;
      void loadList(true);
      // 用户在等待创建时若已选择别处，不抢回焦点。
      return selectionAtStart === detailVersion ? thread.id : null;
    } catch (cause) {
      if (!current(ctx) || controller.signal.aborted) return null;
      if (!recover(cause, ctx.token)) {
        createError.value =
          "创建结果未确认，请先刷新列表确认，再决定是否重试创建。";
      }
      return null;
    } finally {
      if (current(ctx)) creating.value = false;
    }
  }

  function setProject(id: string | null) {
    if (projectId.value !== id) {
      reset();
      projectId.value = id;
    }
  }
  return {
    projectId,
    setProject,
    items,
    selected,
    nextOffset,
    loaded,
    listLoading,
    detailLoading,
    creating,
    listError,
    detailError,
    createError,
    unavailable,
    loadList,
    selectThread,
    clearSelection,
    createThread,
    reset,
  };
});
