import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { request, ApiError } from "../api";
import { randomUuid } from "../uuid";
import { useAuthStore } from "./auth";
import { isId } from "../documents";
import {
  jobPage,
  corpusPage,
  job,
  detail,
  artifact,
  active,
  errorText,
  type JobPage,
  type CorpusPage,
  type Job,
  type Detail,
  type Artifact,
  record,
} from "../evaluations";
const root = "/v1/evaluation-jobs";
export const useEvaluationsStore = defineStore("evaluations", () => {
  const auth = useAuthStore();
  const jobs = ref<JobPage["items"]>([]),
    corpora = ref<CorpusPage["items"]>([]);
  const nextJobs = ref<number | null>(null),
    nextCorpora = ref<number | null>(null);
  const selected = ref<Job | null>(null),
    caseDetail = ref<Detail | null>(null),
    report = ref<Artifact | null>(null),
    comparison = ref<Artifact | null>(null);
  const error = ref(""),
    caseError = ref(""),
    compareError = ref(""),
    loading = ref(false),
    busy = ref(false),
    comparing = ref(false),
    pending = ref<{ key: string; body: unknown } | null>(null);
  const checked = ref(false);
  let mutation = 0;
  let epoch = 0,
    selection = 0,
    caseOrder = 0,
    compareOrder = 0,
    timer: ReturnType<typeof setTimeout> | undefined;
  const controllers = new Set<AbortController>();
  function reset() {
    epoch++;
    selection++;
    caseOrder++;
    compareOrder++;
    clearTimeout(timer);
    controllers.forEach((c) => c.abort());
    controllers.clear();
    jobs.value = [];
    corpora.value = [];
    nextJobs.value = nextCorpora.value = null;
    selected.value = null;
    caseDetail.value = null;
    report.value = comparison.value = null;
    error.value = caseError.value = compareError.value = "";
    loading.value = busy.value = comparing.value = checked.value = false;
    pending.value = null;
  }
  watch(() => auth.user?.id, reset, { flush: "sync" });
  const context = () => ({ epoch, user: auth.user?.id });
  const valid = (ctx: ReturnType<typeof context>) =>
    ctx.epoch === epoch &&
    !!ctx.user &&
    ctx.user === auth.user?.id &&
    auth.status === "authenticated";
  async function call(
    ctx: ReturnType<typeof context>,
    path: string,
    method = "GET",
    body?: unknown,
    key?: string,
  ) {
    if (!valid(ctx) || !auth.accessToken) throw new Error("Identity changed");
    const controller = new AbortController();
    controllers.add(controller);
    const once = (token: string) =>
      request(
        path,
        method,
        body,
        token,
        controller.signal,
        key ? { "Idempotency-Key": key } : undefined,
      );
    const token = auth.accessToken;
    try {
      try {
        return await once(token);
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
          return await once(refreshed);
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
  async function load(kind: "jobs" | "corpora", more = false) {
    const ctx = context();
    loading.value = true;
    error.value = "";
    const offset = more
      ? kind === "jobs"
        ? nextJobs.value
        : nextCorpora.value
      : 0;
    if (offset === null) {
      loading.value = false;
      return;
    }
    try {
      const raw = await call(
        ctx,
        root +
          (kind === "corpora" ? "/corpora" : "") +
          `?offset=${offset}&limit=30`,
      );
      if (!valid(ctx)) return;
      if (kind === "jobs") {
        const parsed = jobPage(raw);
        jobs.value = more ? [...jobs.value, ...parsed.items] : parsed.items;
        nextJobs.value = parsed.next_offset;
      } else {
        const parsed = corpusPage(raw);
        corpora.value = more
          ? [...corpora.value, ...parsed.items]
          : parsed.items;
        nextCorpora.value = parsed.next_offset;
      }
    } catch (cause) {
      if (valid(ctx)) error.value = errorText(cause);
    } finally {
      if (valid(ctx)) loading.value = false;
    }
  }
  async function select(id?: string) {
    const ctx = context(),
      order = ++selection;
    caseOrder++;
    compareOrder++;
    comparing.value = false;
    clearTimeout(timer);
    selected.value = null;
    report.value = comparison.value = null;
    caseDetail.value = null;
    error.value = caseError.value = compareError.value = "";
    if (!id) return;
    if (!isId(id)) {
      error.value = "任务 ID 无效。";
      return;
    }
    async function refresh() {
      const beforeMutation = mutation;
      try {
        const current = job(await call(ctx, `${root}/${id}`));
        if (!valid(ctx) || order !== selection || beforeMutation !== mutation)
          return;
        selected.value = current;
        error.value = "";
        if (
          caseDetail.value &&
          current.cases.find((c) => c.case_id === caseDetail.value?.case_id)
            ?.status !== caseDetail.value.status
        )
          await chooseCase(caseDetail.value.case_id);
        if (!valid(ctx) || order !== selection) return;
        if (current.status === "completed" && !report.value) {
          const raw = await call(ctx, `${root}/${id}/report`);
          if (!valid(ctx) || order !== selection) return;
          if (!record(raw) || raw.report === null)
            throw new Error("Missing report");
          report.value = artifact(raw.report);
        }
      } catch (cause) {
        if (valid(ctx) && order === selection) error.value = errorText(cause);
      } finally {
        if (
          valid(ctx) &&
          order === selection &&
          (!selected.value || active(selected.value.status))
        )
          timer = setTimeout(() => void refresh(), 3000);
      }
    }
    await refresh();
  }
  async function validate(body: unknown) {
    const ctx = context();
    busy.value = true;
    checked.value = false;
    error.value = "";
    try {
      const raw = await call(ctx, root + "/validate", "POST", body);
      if (!valid(ctx)) return false;
      if (!record(raw) || raw.status !== "valid" || raw.mode !== "controlled")
        throw new Error("Invalid validation");
      checked.value = true;
      return true;
    } catch (cause) {
      if (valid(ctx)) error.value = errorText(cause);
      return false;
    } finally {
      if (valid(ctx)) busy.value = false;
    }
  }
  async function create(body: unknown) {
    const ctx = context(),
      order = selection;
    if (busy.value) return null;
    busy.value = true;
    error.value = "";
    pending.value ??= { key: randomUuid(), body };
    try {
      const raw = await call(
        ctx,
        root,
        "POST",
        pending.value.body,
        pending.value.key,
      );
      if (!valid(ctx)) return null;
      if (!record(raw) || !isId(raw.job_id)) throw new Error("Invalid receipt");
      pending.value = null;
      checked.value = false;
      await load("jobs");
      return valid(ctx) && order === selection ? raw.job_id : null;
    } catch (cause) {
      if (valid(ctx))
        error.value =
          errorText(cause) + " 创建结果尚未确认，请重试原提交或查看历史。";
      return null;
    } finally {
      if (valid(ctx)) busy.value = false;
    }
  }
  async function cancel() {
    const id = selected.value?.job_id;
    if (!id || busy.value) return;
    const ctx = context(),
      order = selection;
    busy.value = true;
    mutation++;
    try {
      const current = job(await call(ctx, `${root}/${id}/cancel`, "POST"));
      if (valid(ctx) && order === selection) selected.value = current;
    } catch (cause) {
      if (valid(ctx) && order === selection) error.value = errorText(cause);
    } finally {
      if (valid(ctx)) busy.value = false;
    }
  }
  async function chooseCase(key: string) {
    const ctx = context(),
      id = selected.value?.job_id,
      order = ++caseOrder;
    caseDetail.value = null;
    caseError.value = "";
    if (!id) return;
    try {
      const value = detail(
        await call(ctx, `${root}/${id}/cases/${encodeURIComponent(key)}`),
      );
      if (valid(ctx) && order === caseOrder && selected.value?.job_id === id)
        caseDetail.value = value;
    } catch (cause) {
      if (valid(ctx) && order === caseOrder) caseError.value = errorText(cause);
    }
  }
  async function compare(baseline: string, changes: string[]) {
    const ctx = context(),
      id = selected.value?.job_id,
      order = ++compareOrder;
    comparison.value = null;
    compareError.value = "";
    if (!id || !isId(baseline)) return;
    comparing.value = true;
    try {
      const value = artifact(
        await call(ctx, `${root}/${id}/compare`, "POST", {
          baseline_id: baseline,
          policy: { allowed_modes: ["controlled"], permitted_changes: changes },
        }),
      );
      if (valid(ctx) && order === compareOrder && selected.value?.job_id === id)
        comparison.value = value;
    } catch (cause) {
      if (valid(ctx) && order === compareOrder)
        compareError.value = errorText(cause);
    } finally {
      if (valid(ctx) && order === compareOrder) comparing.value = false;
    }
  }
  function clearComparison() {
    compareOrder++;
    comparison.value = null;
    comparing.value = false;
    compareError.value = "";
  }
  return {
    jobs,
    corpora,
    nextJobs,
    nextCorpora,
    selected,
    caseDetail,
    report,
    comparison,
    error,
    caseError,
    compareError,
    loading,
    busy,
    comparing,
    pending,
    checked,
    reset,
    load,
    select,
    validate,
    create,
    cancel,
    chooseCase,
    compare,
    clearComparison,
  };
});
