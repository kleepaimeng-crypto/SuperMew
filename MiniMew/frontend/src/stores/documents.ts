import { defineStore } from "pinia";
import { ref, shallowRef, watch } from "vue";
import { ApiError, request } from "../api";
import {
  activeJob,
  isId,
  parseDocument,
  parseEmbeddingSettings,
  parseJob,
  parsePage,
  parseReceipt,
  parseVersion,
  type Document,
  type EmbeddingChange,
  type Version,
} from "../documents";
import { useAuthStore } from "./auth";

export const useDocumentsStore = defineStore("documents", () => {
  const auth = useAuthStore();
  const projectId = ref<string | null>(null),
    archived = ref(false);
  const items = ref<Document[]>([]),
    selected = ref<Document | null>(null);
  const currentVersion = ref<Version | null>(null),
    latestVersion = ref<Version | null>(null);
  const nextOffset = ref<number | null>(null),
    loading = ref(false),
    detailLoading = ref(false),
    uploading = ref(false);
  const listError = ref(""),
    detailError = ref(""),
    progressError = ref(""),
    uploadError = ref("");
  const pendingUpload = shallowRef<{
    file: File;
    target?: string;
    key: string;
  } | null>(null);
  let epoch = 0,
    sequence = 0,
    selection = 0,
    listSequence = 0,
    mounted = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let polling = false;
  const controllers = new Set<AbortController>(),
    accepted = new Map<string, number>();
  function stopTimer() {
    clearTimeout(timer);
    timer = undefined;
  }
  function reset() {
    ++epoch;
    ++selection;
    mounted = false;
    polling = false;
    stopTimer();
    controllers.forEach((c) => c.abort());
    controllers.clear();
    accepted.clear();
    items.value = [];
    selected.value = null;
    currentVersion.value = latestVersion.value = null;
    nextOffset.value = null;
    loading.value = detailLoading.value = uploading.value = false;
    listError.value =
      detailError.value =
      progressError.value =
      uploadError.value =
        "";
    pendingUpload.value = null;
  }
  watch(
    () => auth.user?.id,
    () => {
      projectId.value = null;
      archived.value = false;
      reset();
    },
    { flush: "sync" },
  );
  function context() {
    return { epoch, userId: auth.user?.id };
  }
  function valid(ctx: ReturnType<typeof context>) {
    return (
      ctx.epoch === epoch &&
      !!ctx.userId &&
      ctx.userId === auth.user?.id &&
      auth.status === "authenticated"
    );
  }
  async function call(
    ctx: ReturnType<typeof context>,
    path: string,
    method = "GET",
    body?: unknown,
  ) {
    if (!valid(ctx) || !auth.accessToken) throw new Error("Identity changed");
    const controller = new AbortController();
    controllers.add(controller);
    const token = auth.accessToken;
    try {
      try {
        return await request(path, method, body, token, controller.signal);
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 401 || !valid(ctx))
          throw cause;
        if (
          !(await auth.refreshAccess(token)) ||
          !valid(ctx) ||
          !auth.accessToken
        )
          throw cause;
        const refreshed = auth.accessToken;
        try {
          return await request(
            path,
            method,
            body,
            refreshed,
            controller.signal,
          );
        } catch (again) {
          if (again instanceof ApiError && again.status === 401)
            auth.requireRecovery(refreshed);
          throw again;
        }
      }
    } finally {
      controllers.delete(controller);
    }
  }
  function apply(doc: Document, order: number) {
    if ((accepted.get(doc.id) ?? 0) > order) return;
    accepted.set(doc.id, order);
    items.value = items.value.map((item) => (item.id === doc.id ? doc : item));
    if (selected.value?.id === doc.id) selected.value = doc;
  }
  function schedule() {
    stopTimer();
    if (
      !mounted ||
      polling ||
      progressError.value ||
      (!items.value.some((d) => activeJob(d.latest_job)) &&
        !activeJob(selected.value?.latest_job))
    )
      return;
    timer = setTimeout(() => void poll(), 2000);
  }
  async function loadList(restart = false) {
    const ctx = context();
    if (!valid(ctx) || (loading.value && !restart)) return;
    if (!restart && items.value.length && nextOffset.value === null) return;
    const order = ++sequence;
    listSequence = order;
    loading.value = true;
    listError.value = "";
    const offset = restart ? 0 : (nextOffset.value ?? 0);
    try {
      const page = parsePage(
        await call(
          ctx,
          `/v1/documents?limit=20&offset=${offset}${projectId.value ? "&project_id=" + projectId.value : ""}${archived.value ? "&archived=true" : ""}`,
        ),
      );
      if (!valid(ctx) || listSequence !== order) return;
      if (page.next_offset !== null && page.next_offset <= offset)
        throw new Error("Invalid pagination");
      const incoming = page.items.map((doc) =>
        (accepted.get(doc.id) ?? 0) > order
          ? (items.value.find((i) => i.id === doc.id) ??
            (selected.value?.id === doc.id ? selected.value : doc))
          : doc,
      );
      page.items.forEach((doc) => apply(doc, order));
      items.value = restart
        ? incoming
        : [
            ...items.value,
            ...incoming.filter((d) => !items.value.some((i) => i.id === d.id)),
          ];
      nextOffset.value = page.next_offset;
      schedule();
    } catch {
      if (valid(ctx) && listSequence === order)
        listError.value = "文档目录加载失败，请重试。";
    } finally {
      if (valid(ctx) && listSequence === order) loading.value = false;
    }
  }
  async function versions(
    doc: Document,
    ctx: ReturnType<typeof context>,
    turn: number,
  ) {
    const latestId = doc.latest_job?.version_id;
    const ids = [
      ...new Set(
        [doc.current_version_id, latestId].filter((id): id is string => !!id),
      ),
    ];
    const values = await Promise.all(
      ids.map(async (id) => {
        const version = parseVersion(
          await call(ctx, `/v1/documents/${doc.id}/versions/${id}`),
        );
        if (version.document_id !== doc.id || version.id !== id)
          throw new Error("Version scope mismatch");
        return version;
      }),
    );
    if (
      !valid(ctx) ||
      selection !== turn ||
      selected.value?.id !== doc.id ||
      selected.value.current_version_id !== doc.current_version_id ||
      selected.value.latest_job?.version_id !== latestId
    )
      return;
    currentVersion.value =
      values.find((v) => v.id === doc.current_version_id) ?? null;
    latestVersion.value = values.find((v) => v.id === latestId) ?? null;
  }
  async function refreshDocument(
    id: string,
    ctx: ReturnType<typeof context>,
    readJob = false,
  ) {
    const order = ++sequence,
      turn = selection;
    if (readJob && selected.value?.id === id && selected.value.latest_job) {
      const expected = selected.value.latest_job;
      const job = parseJob(await call(ctx, `/v1/index-jobs/${expected.id}`));
      if (
        job.id !== expected.id ||
        job.document_id !== id ||
        job.version_id !== expected.version_id
      )
        throw new Error("Job scope mismatch");
    }
    const doc = parseDocument(await call(ctx, `/v1/documents/${id}`));
    if (!valid(ctx)) return;
    if (doc.id !== id) throw new Error("Document scope mismatch");
    apply(doc, order);
    if (
      selected.value?.id === id &&
      turn === selection &&
      (currentVersion.value?.id !== doc.current_version_id ||
        latestVersion.value?.id !== doc.latest_job?.version_id)
    ) {
      await versions(doc, ctx, turn);
    }
  }
  async function selectDocument(id?: string) {
    const ctx = context(),
      turn = ++selection;
    selected.value = null;
    currentVersion.value = latestVersion.value = null;
    detailError.value = "";
    detailLoading.value = false;
    if (!id) {
      schedule();
      return;
    }
    if (!valid(ctx) || !isId(id)) {
      detailError.value = "找不到这份文档，请从目录重新选择。";
      return;
    }
    detailLoading.value = true;
    const order = ++sequence;
    try {
      const doc = parseDocument(await call(ctx, `/v1/documents/${id}`));
      if (!valid(ctx) || selection !== turn) return;
      if (doc.id !== id || (doc.project_id ?? null) !== projectId.value)
        throw new Error("Document scope mismatch");
      selected.value = doc;
      apply(doc, order);
      await versions(doc, ctx, turn);
      schedule();
    } catch {
      if (valid(ctx) && selection === turn)
        detailError.value = "文档加载失败或无权访问，请重试或从目录重新选择。";
    } finally {
      if (valid(ctx) && selection === turn) detailLoading.value = false;
    }
  }
  async function poll() {
    stopTimer();
    const ctx = context();
    if (!mounted || polling || !valid(ctx)) return;
    polling = true;
    const ids = [
      ...new Set([
        ...items.value.filter((d) => activeJob(d.latest_job)).map((d) => d.id),
        ...(activeJob(selected.value?.latest_job) && selected.value
          ? [selected.value.id]
          : []),
      ]),
    ];
    try {
      for (let start = 0; start < ids.length; start += 4) {
        await Promise.all(
          ids
            .slice(start, start + 4)
            .map((id) => refreshDocument(id, ctx, true)),
        );
        if (!mounted || !valid(ctx)) return;
      }
      if (valid(ctx)) progressError.value = "";
    } catch {
      if (valid(ctx))
        progressError.value = "进度暂时无法更新，正在显示上次结果。请重试。";
    } finally {
      if (valid(ctx)) {
        polling = false;
        schedule();
      }
    }
  }
  async function refresh() {
    progressError.value = "";
    const ctx = context();
    await loadList(true);
    if (selected.value && valid(ctx)) {
      try {
        await refreshDocument(selected.value.id, ctx, true);
      } catch {
        if (valid(ctx))
          progressError.value = "进度暂时无法更新，正在显示上次结果。请重试。";
      }
    }
    schedule();
  }
  async function submit() {
    const intent = pendingUpload.value,
      ctx = context(),
      turn = selection;
    if (!intent || !valid(ctx) || uploading.value) return null;
    uploading.value = true;
    uploadError.value = "";
    const form = new FormData();
    form.append("file", intent.file);
    form.append("idempotency_key", intent.key);
    if (projectId.value && !intent.target)
      form.append("project_id", projectId.value);
    try {
      const result = parseReceipt(
        await call(
          ctx,
          intent.target
            ? `/v1/documents/${intent.target}/versions`
            : "/v1/documents",
          "POST",
          form,
        ),
      );
      if (!valid(ctx)) return null;
      if (intent.target && result.document.id !== intent.target)
        throw new Error("Upload scope mismatch");
      pendingUpload.value = null;
      await loadList(true);
      return selection === turn ? result.document.id : null;
    } catch (cause) {
      const conflict =
        cause instanceof ApiError && cause.status === 409
          ? await cause.errorCode()
          : undefined;
      if (valid(ctx)) {
        uploadError.value =
          cause instanceof ApiError && [413, 422].includes(cause.status)
            ? "文件未被接受。请使用不超过5 MiB的非空UTF-8 Markdown或TXT文件。"
            : conflict === "EMBEDDING_REBUILD_IN_PROGRESS"
              ? "此知识库正在切换编码模型。请等重建完成后重试上传。"
              : cause instanceof ApiError && [403, 404].includes(cause.status)
                ? "这份文档不可访问，请刷新目录后重试。"
                : "上传结果尚未确认。可刷新目录查看，或重试本次上传；重试沿用原提交标识。";
      }
      return null;
    } finally {
      if (valid(ctx)) uploading.value = false;
    }
  }
  async function upload(file: File, target?: string) {
    if (uploading.value) return null;
    if (
      !/\.(md|txt)$/i.test(file.name) ||
      file.size === 0 ||
      file.size > 5 * 1024 * 1024
    ) {
      uploadError.value = "请选择不超过5 MiB的非空Markdown或TXT文件。";
      return null;
    }
    if (
      pendingUpload.value?.file !== file ||
      pendingUpload.value.target !== target
    )
      pendingUpload.value = { file, target, key: crypto.randomUUID() };
    return submit();
  }
  function activate() {
    mounted = true;
    schedule();
  }
  async function embeddingSettings(
    project: string | null,
    change?: EmbeddingChange,
  ) {
    const params = project
      ? "?" + new URLSearchParams({ project_id: project })
      : "";
    return parseEmbeddingSettings(
      await call(
        context(),
        "/v1/embedding-profiles/selection" + params,
        change ? "POST" : "GET",
        change,
      ),
    );
  }
  function setScope(id: string | null, showArchived = false) {
    if (projectId.value !== id || archived.value !== showArchived) {
      reset();
      projectId.value = id;
      archived.value = showArchived;
    }
  }
  async function withdraw() {
    const doc = selected.value,
      ctx = context(),
      turn = selection;
    if (!doc || uploading.value) return;
    uploading.value = true;
    try {
      const changed = parseDocument(
        await call(ctx, `/v1/documents/${doc.id}`, "DELETE"),
      );
      if (!valid(ctx) || turn !== selection) return;
      selected.value = changed;
      apply(changed, ++sequence);
      await loadList(true);
    } catch {
      if (valid(ctx))
        detailError.value = "下线未确认，请刷新确认文档状态后重试。";
    } finally {
      if (valid(ctx)) uploading.value = false;
    }
  }
  return {
    projectId,
    archived,
    setScope,
    withdraw,
    embeddingSettings,
    items,
    selected,
    currentVersion,
    latestVersion,
    nextOffset,
    loading,
    detailLoading,
    uploading,
    listError,
    detailError,
    progressError,
    uploadError,
    pendingUpload,
    loadList,
    selectDocument,
    refresh,
    poll,
    upload,
    retryUpload: submit,
    activate,
    reset,
  };
});
