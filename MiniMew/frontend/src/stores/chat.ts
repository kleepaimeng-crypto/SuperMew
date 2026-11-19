import { computed, reactive, ref, watch } from "vue";
import { defineStore } from "pinia";
import { ApiError, request } from "../api";
import { parseAnswer, type AnswerView } from "../answers";
import {
  parseMessagePage,
  parseRun,
  terminal,
  type ChatMessage,
  type ChatRun,
} from "../chat";
import {
  EventProtocolError,
  isRecord,
  isDateTime,
  isUuid,
  parseRunEvent,
} from "../events/runEventContract";
import {
  createRunProjection,
  reduceRunEvent,
  type RunProjection,
} from "../events/runEventReducer";
import { readRunEvents, waitForRetry } from "../events/runEventStream";
import { failureForRun } from "../runErrors";
import {
  parseSkills,
  parseSelection,
  type SkillSummary,
  type SkillSelection,
} from "../skills";
import { useAuthStore } from "./auth";

interface Intent {
  key: string;
  content: string;
  skill?: SkillSelection;
  skillName?: string; // 仅本地表单显示，不参与服务端选择、幂等或权限。
}
export interface RunView {
  run: ChatRun;
  projection: RunProjection;
  replay: boolean;
  connection:
    "waiting" | "connecting" | "live" | "retrying" | "error" | "closed";
  error: string;
  cancelBusy: boolean;
  cancelError: string;
  activityLoading: boolean;
  activityLoaded: boolean;
  activityError: string;
  resumeDraft: string;
  resumeBusy: boolean;
  resumePending: boolean;
  resumeAccepted: boolean;
  resumeError: string;
}
const pendingPrefix = "minimew.chat-pending:";
class EventGap extends Error {}

export const useChatStore = defineStore("chat", () => {
  const auth = useAuthStore();
  const threadId = ref<string | null>(null);
  const messages = ref<ChatMessage[]>([]);
  const runs = reactive<Record<string, RunView>>({});
  const answers = reactive<Record<string, AnswerView>>({});
  const answerRequests = new Map<
    string,
    { content: string; promise: Promise<void> }
  >();
  const historyLoading = ref(false);
  const historyLoaded = ref(false);
  const historyError = ref("");
  const previousCursor = ref<number | null>(null);
  const draft = ref("");
  const skills = ref<SkillSummary[]>([]);
  const selectedSkill = ref("");
  const skillsLoading = ref(false);
  const skillsError = ref("");
  const pending = ref<Intent | null>(null);
  const submitting = ref(false);
  const submitError = ref("");
  const acceptedRunId = ref<string | null>(null);
  let epoch = 0;
  let historyVersion = 0;
  let owner = new AbortController();
  let historyController: AbortController | undefined;
  const subscriptions = new Map<string, AbortController>();
  // 一次性凭据只保留在当前页面闭包，既不持久化也不进入事件投影。
  const resumeIntents = new Map<
    string,
    {
      checkpoint_id: string;
      token: string;
      idempotency_key: string;
      answer: string;
    }
  >();

  function storageKey(userId: string, thread: string) {
    return `${pendingPrefix}${userId}:${thread}`;
  }
  function saveIntent(value: Intent | null) {
    if (!auth.user || !threadId.value) return;
    try {
      const key = storageKey(auth.user.id, threadId.value);
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    } catch {
      /* 存储禁用时本次页面内仍保留相同提交意图。 */
    }
  }
  function restoreIntent(userId: string, thread: string): Intent | null {
    try {
      const raw = sessionStorage.getItem(storageKey(userId, thread));
      const value: unknown = raw ? JSON.parse(raw) : null;
      if (
        isRecord(value) &&
        typeof value.key === "string" &&
        /^[\w.:-]{1,128}$/.test(value.key) &&
        typeof value.content === "string" &&
        value.content.trim() &&
        [...value.content].length <= 20000
      )
        return {
          key: value.key,
          content: value.content,
          ...(value.skill !== undefined
            ? { skill: parseSelection(value.skill) }
            : {}),
          ...(typeof value.skillName === "string" &&
          value.skillName.trim() &&
          [...value.skillName].length <= 80 &&
          !value.skillName.includes("\0")
            ? { skillName: value.skillName }
            : {}),
        };
    } catch {
      /* 无可恢复的本地草稿。 */
    }
    return null;
  }
  function close() {
    ++epoch;
    ++historyVersion;
    owner.abort();
    historyController?.abort();
    subscriptions.forEach((controller) => controller.abort());
    subscriptions.clear();
    resumeIntents.clear();
    owner = new AbortController();
    threadId.value = null;
    messages.value = [];
    Object.keys(runs).forEach((key) => delete runs[key]);
    Object.keys(answers).forEach((key) => delete answers[key]);
    answerRequests.clear();
    historyLoading.value = false;
    historyLoaded.value = false;
    historyError.value = "";
    previousCursor.value = null;
    draft.value = "";
    skills.value = [];
    selectedSkill.value = "";
    skillsLoading.value = false;
    skillsError.value = "";
    pending.value = null;
    submitting.value = false;
    submitError.value = "";
    acceptedRunId.value = null;
  }
  watch(
    () => auth.user?.id,
    (next, previous) => {
      if (next === previous) return;
      close();
      if (previous && (next || auth.logoutPending)) {
        try {
          Object.keys(sessionStorage)
            .filter((key) => key.startsWith(`${pendingPrefix}${previous}:`))
            .forEach((key) => sessionStorage.removeItem(key));
        } catch {
          /* 清理本地草稿不阻碍退出。 */
        }
      }
    },
    { flush: "sync" },
  );
  watch(
    () => auth.status,
    (status) => {
      if (status !== "anonymous") return;
      try {
        Object.keys(sessionStorage)
          .filter((key) => key.startsWith(pendingPrefix))
          .forEach((key) => sessionStorage.removeItem(key));
      } catch {
        /* 不保存凭据，清理失败也不恢复旧账号视图。 */
      }
    },
    { flush: "sync" },
  );

  function context() {
    if (
      !auth.user ||
      !auth.accessToken ||
      auth.status !== "authenticated" ||
      !threadId.value
    )
      return null;
    return {
      epoch,
      userId: auth.user.id,
      threadId: threadId.value,
      signal: owner.signal,
    };
  }
  type Context = NonNullable<ReturnType<typeof context>>;
  function current(ctx: Context) {
    return (
      ctx.epoch === epoch &&
      ctx.userId === auth.user?.id &&
      ctx.threadId === threadId.value &&
      !ctx.signal.aborted
    );
  }
  async function authorized(
    ctx: Context,
    path: string,
    method = "GET",
    body?: unknown,
    signal = ctx.signal,
  ) {
    if (!current(ctx) || !auth.accessToken)
      throw new DOMException("Aborted", "AbortError");
    const token = auth.accessToken;
    try {
      return await request(path, method, body, token, signal);
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 401) throw cause;
      if (
        !(await auth.refreshAccess(token)) ||
        !current(ctx) ||
        !auth.accessToken
      )
        throw cause;
      return request(path, method, body, auth.accessToken, signal);
    }
  }
  const runPath = (ctx: Context, id: string) =>
    `/v1/threads/${ctx.threadId}/runs/${id}`;

  async function loadSkills() {
    const ctx = context();
    if (!ctx || skillsLoading.value) return;
    skillsLoading.value = true;
    skillsError.value = "";
    try {
      const items = parseSkills(await authorized(ctx, "/v1/skills"));
      if (current(ctx)) skills.value = items;
    } catch {
      if (current(ctx))
        skillsError.value = "任务模式暂时无法加载，默认助手仍可使用。";
    } finally {
      if (current(ctx)) skillsLoading.value = false;
    }
  }

  function attachRun(run: ChatRun, replay: boolean): RunView {
    let view = runs[run.id];
    if (
      view &&
      (view.run.assistant_message_id !== run.assistant_message_id ||
        view.run.user_message_id !== run.user_message_id)
    )
      throw new EventProtocolError();
    if (!view) {
      view = {
        run,
        projection: createRunProjection({
          threadId: run.thread_id,
          runId: run.id,
          assistantMessageId: run.assistant_message_id,
        }),
        replay,
        connection: replay ? "waiting" : "closed",
        error: "",
        cancelBusy: false,
        cancelError: "",
        activityLoading: false,
        activityLoaded: false,
        activityError: "",
        resumeDraft: "",
        resumeBusy: false,
        resumePending: false,
        resumeAccepted: false,
        resumeError: "",
      };
      runs[run.id] = view;
    } else {
      // 终态不可被迟到的查询响应回退；重放状态只由 reducer 更新。
      if (!terminal(view.run.status)) view.run = run;
      view.replay ||= replay;
    }
    return runs[run.id]!;
  }

  async function loadHistory(older = false): Promise<void> {
    const ctx = context();
    if (
      !ctx ||
      (older && (historyLoading.value || previousCursor.value === null))
    )
      return;
    const before = older ? previousCursor.value! : undefined;
    const version = ++historyVersion;
    historyController?.abort();
    const controller = (historyController = new AbortController());
    const signal = AbortSignal.any([ctx.signal, controller.signal]);
    historyLoading.value = true;
    historyError.value = "";
    try {
      const page = parseMessagePage(
        await authorized(
          ctx,
          `/v1/threads/${ctx.threadId}/messages?limit=50${before === undefined ? "" : `&before=${before}`}`,
          "GET",
          undefined,
          signal,
        ),
        ctx.threadId,
        before,
      );
      if (!current(ctx) || version !== historyVersion) return;
      const merged = older ? [...page.items, ...messages.value] : page.items;
      messages.value = [
        ...new Map(merged.map((message) => [message.id, message])).values(),
      ].sort((a, b) => a.sequence - b.sequence);
      previousCursor.value = page.previous_cursor;
      historyLoaded.value = true;
      const assistants = page.items.filter(
        (message) => message.role === "assistant",
      );
      // 查询逐条串行，避免翻页时瞬间占满 HTTP 连接；SSE 另有两个连接上限。
      for (const message of assistants) {
        if (!current(ctx) || version !== historyVersion) return;
        if (message.status === "completed") void loadAnswer(message.run_id);
        const existing = runs[message.run_id];
        if (existing) continue;
        const run = parseRun(
          await authorized(
            ctx,
            runPath(ctx, message.run_id),
            "GET",
            undefined,
            signal,
          ),
          ctx.threadId,
          message.run_id,
        );
        if (!current(ctx) || version !== historyVersion) return;
        if (run.assistant_message_id !== message.id)
          throw new EventProtocolError();
        attachRun(run, message.status === "pending" || !terminal(run.status));
        schedule(ctx);
      }
    } catch (cause) {
      if (!current(ctx) || signal.aborted || version !== historyVersion) return;
      historyError.value =
        cause instanceof ApiError && [403, 404].includes(cause.status)
          ? "消息不存在或不可访问，请重新选择对话。"
          : "历史或任务状态加载失败，请重试同步。";
    } finally {
      if (current(ctx) && version === historyVersion)
        historyLoading.value = false;
    }
  }

  async function open(id: string) {
    close();
    if (!isUuid(id) || !auth.user) return;
    threadId.value = id;
    pending.value = restoreIntent(auth.user.id, id);
    if (pending.value) {
      draft.value = pending.value.content;
      if (pending.value.skill)
        selectedSkill.value = `${pending.value.skill.id}@${pending.value.skill.version}`;
      submitError.value =
        "上次提交结果未确认，可重试原提交；请勿重复创建问题。";
    }
    await loadHistory();
  }

  async function submit(): Promise<boolean> {
    const ctx = context();
    if (
      !ctx ||
      submitting.value ||
      (!pending.value &&
        (!draft.value.trim() ||
          [...draft.value].length > 20000 ||
          draft.value.includes("\0")))
    )
      return false;
    if (!pending.value) {
      const chosen = skills.value.find(
        (s) => `${s.id}@${s.version}` === selectedSkill.value,
      );
      if (selectedSkill.value && !chosen) {
        submitError.value = "所选任务模式不可用，请重新加载或选择默认助手。";
        return false;
      }
      pending.value = {
        key: crypto.randomUUID(),
        content: draft.value,
        ...(chosen
          ? {
              skill: { id: chosen.id, version: chosen.version },
              skillName: chosen.name,
            }
          : {}),
      };
    }
    const intent = { ...pending.value };
    saveIntent(intent);
    submitting.value = true;
    submitError.value = "";
    try {
      const run = parseRun(
        await authorized(ctx, `/v1/threads/${ctx.threadId}/runs`, "POST", {
          idempotency_key: intent.key,
          content: intent.content,
          ...(intent.skill ? { skill: intent.skill } : {}),
        }),
        ctx.threadId,
      );
      if (!current(ctx)) return false;
      attachRun(run, true);
      acceptedRunId.value = run.id;
      pending.value = null;
      if (
        selectedSkill.value &&
        !skills.value.some(
          (s) => `${s.id}@${s.version}` === selectedSkill.value,
        )
      )
        selectedSkill.value = "";
      saveIntent(null);
      draft.value = "";
      schedule(ctx);
      await loadHistory();
      return current(ctx);
    } catch (cause) {
      if (!current(ctx)) return false;
      submitError.value =
        cause instanceof ApiError && [409, 422].includes(cause.status)
          ? "提交被拒绝或幂等键冲突，请检查历史后修改本地草稿。"
          : "提交结果未确认。重试会使用原正文和原幂等键，不会另建一次提问。";
      return false;
    } finally {
      if (current(ctx)) submitting.value = false;
    }
  }
  function discardPending() {
    if (submitting.value) return;
    pending.value = null;
    saveIntent(null);
    submitError.value = "";
  }

  async function synchronizeRun(id: string) {
    const ctx = context();
    const view = runs[id];
    if (!ctx || !view) return;
    try {
      const run = parseRun(
        await authorized(ctx, runPath(ctx, id)),
        ctx.threadId,
        id,
      );
      if (!current(ctx)) return;
      attachRun(run, view.replay);
      view.cancelError = "";
      await loadHistory();
    } catch {
      if (current(ctx))
        view.cancelError = "状态暂未确认，请稍后查询或重试原取消请求。";
    }
  }
  async function cancel(id: string) {
    const ctx = context();
    const view = runs[id];
    if (!ctx || !view || view.cancelBusy || terminal(runStatus(view))) return;
    view.cancelBusy = true;
    view.cancelError = "";
    try {
      const run = parseRun(
        await authorized(ctx, `${runPath(ctx, id)}/cancel`, "POST"),
        ctx.threadId,
        id,
      );
      if (current(ctx)) attachRun(run, true);
    } catch {
      if (current(ctx))
        view.cancelError = "取消结果未确认，可查询状态或重试取消。";
    } finally {
      if (current(ctx)) view.cancelBusy = false;
    }
  }

  async function resumeRun(id: string): Promise<void> {
    const ctx = context(),
      view = runs[id];
    const waiting = view?.projection.waiting;
    if (
      !ctx ||
      !view ||
      !waiting ||
      view.resumeBusy ||
      view.cancelBusy ||
      view.projection.cancelRequestedAt ||
      view.run.cancel_requested_at ||
      view.resumeAccepted ||
      runStatus(view) !== "waiting_input"
    )
      return;
    if (
      !view.resumeDraft.trim() ||
      [...view.resumeDraft].length > 1024 ||
      view.resumeDraft.includes("\0")
    )
      return;
    view.resumeBusy = true;
    view.resumeError = "";
    try {
      let intent = resumeIntents.get(id);
      if (!intent) {
        const credential = await authorized(
          ctx,
          `${runPath(ctx, id)}/resume-credential`,
          "POST",
        );
        if (!current(ctx)) return;
        if (
          !isRecord(credential) ||
          credential.checkpoint_id !== waiting.checkpointId ||
          typeof credential.token !== "string" ||
          !/^[A-Za-z0-9_-]{32,128}$/.test(credential.token) ||
          typeof credential.prompt !== "string" ||
          !isDateTime(credential.expires_at)
        )
          throw new EventProtocolError();
        intent = {
          checkpoint_id: waiting.checkpointId,
          token: credential.token,
          idempotency_key: crypto.randomUUID(),
          answer: view.resumeDraft,
        };
        resumeIntents.set(id, intent);
        view.resumePending = true;
      }
      const run = parseRun(
        await authorized(ctx, `${runPath(ctx, id)}/resume`, "POST", intent),
        ctx.threadId,
        id,
      );
      if (!current(ctx)) return;
      attachRun(run, true);
      resumeIntents.delete(id);
      view.resumePending = false;
      view.resumeAccepted = true;
      view.resumeDraft = "";
      schedule(ctx);
    } catch (cause) {
      if (!current(ctx)) return;
      if (cause instanceof ApiError && [404, 409, 422].includes(cause.status)) {
        resumeIntents.delete(id);
        view.resumePending = false;
        view.resumeError =
          "等待已结束、凭据已更新或补充被拒绝，请同步任务状态。";
        await synchronizeRun(id);
      } else {
        view.resumeError = "补充结果尚未确认，请重试；会沿用原补充和幂等键。";
      }
    } finally {
      if (current(ctx)) view.resumeBusy = false;
    }
  }

  function schedule(ctx: Context) {
    if (!current(ctx)) return;
    for (const view of Object.values(runs)) {
      if (subscriptions.size >= 2) break;
      if (
        !view.replay ||
        view.connection === "error" ||
        terminal(view.projection.runStatus) ||
        subscriptions.has(view.run.id)
      )
        continue;
      const controller = new AbortController();
      subscriptions.set(view.run.id, controller);
      void follow(ctx, view, controller).finally(() => {
        if (subscriptions.get(view.run.id) === controller)
          subscriptions.delete(view.run.id);
        if (current(ctx)) schedule(ctx);
      });
    }
  }
  async function follow(
    ctx: Context,
    view: RunView,
    controller: AbortController,
  ) {
    const signal = AbortSignal.any([ctx.signal, controller.signal]);
    let failures = 0;
    while (
      current(ctx) &&
      !signal.aborted &&
      !terminal(view.projection.runStatus)
    ) {
      view.connection = failures ? "retrying" : "connecting";
      const token = auth.accessToken;
      if (!token) return;
      try {
        const end = await readRunEvents(
          `${runPath(ctx, view.run.id)}/events/stream?after=${view.projection.cursor}`,
          token,
          signal,
          (event) => {
            if (!current(ctx) || signal.aborted) return false;
            const result = reduceRunEvent(view.projection, event);
            if (result.kind === "gap") throw new EventGap();
            if (result.kind === "foreign" || result.kind === "invalid")
              throw new EventProtocolError();
            if (result.kind === "applied") {
              view.projection = result.state;
              failures = 0;
              view.error = "";
              view.connection = "live";
              if (event.type === "run.completed") void loadAnswer(view.run.id);
            }
            return !terminal(view.projection.runStatus);
          },
        );
        if (!current(ctx) || signal.aborted) return;
        if (terminal(view.projection.runStatus)) {
          view.activityLoaded = true;
          view.connection = "closed";
          view.replay = false;
          view.cancelError = "";
          void loadHistory();
          return;
        }
        if (end === "consumed") throw new EventProtocolError();
      } catch (cause) {
        if (!current(ctx) || signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) {
          try {
            if ((await auth.refreshAccess(token)) && current(ctx)) continue;
          } catch {
            /* 临时续期故障保留正文，按原游标继续尝试。 */
          }
          if (!current(ctx)) return;
        } else if (
          cause instanceof EventProtocolError ||
          (cause instanceof ApiError && [403, 404, 422].includes(cause.status))
        ) {
          view.connection = "error";
          view.error = "事件无法安全应用，请重试同步。";
          return;
        }
      }
      ++failures;
      view.connection = "retrying";
      view.error = "连接中断，正在从已处理的位置恢复。";
      await waitForRetry(
        Math.min(10000, 500 * 2 ** Math.min(failures - 1, 5)),
        signal,
      );
    }
  }
  function retryRun(id: string) {
    const ctx = context();
    const view = runs[id];
    if (!ctx || !view || subscriptions.has(id)) return;
    // 协议故障从空投影重建，不在完整历史正文上再次拼接。
    if (view.connection === "error")
      view.projection = createRunProjection({
        threadId: ctx.threadId,
        runId: id,
        assistantMessageId: view.run.assistant_message_id,
      });
    view.connection = "waiting";
    view.error = "";
    view.replay = true;
    schedule(ctx);
  }

  async function loadAnswer(id: string, retry = false): Promise<void> {
    const ctx = context();
    const message = displayMessages.value.find(
      (m) => m.run_id === id && m.role === "assistant",
    );
    if (!ctx || !message || message.status !== "completed") return;
    const inflight = answerRequests.get(id);
    if (inflight?.content === message.content) return inflight.promise;
    const cached = answers[id];
    if (
      cached?.content === message.content &&
      cached.messageId === message.id &&
      (cached.state === "loaded" || !retry)
    )
      return;
    answers[id] = {
      messageId: message.id,
      content: message.content,
      state: "loading",
      materials: null,
      error: "",
    };
    const task = (async () => {
      try {
        const response = await authorized(ctx, `${runPath(ctx, id)}/answer`);
        const materials = parseAnswer(response, message);
        if (
          !current(ctx) ||
          displayMessages.value.find((m) => m.id === message.id)?.content !==
            message.content
        )
          return;
        answers[id] = {
          messageId: message.id,
          content: message.content,
          state: "loaded",
          ...(typeof response === "object" &&
          response !== null &&
          "withdrawn_document_ids" in response
            ? { withdrawnDocIds: response.withdrawn_document_ids as string[] }
            : {}),
          materials,
          error: "",
        };
      } catch {
        if (
          !current(ctx) ||
          answers[id]?.content !== message.content ||
          answers[id]?.messageId !== message.id
        )
          return;
        answers[id] = {
          messageId: message.id,
          content: message.content,
          state: "error",
          materials: null,
          error: "引用资料暂时无法读取，回答已保存。",
        };
      }
    })();
    const entry = { content: message.content, promise: task };
    answerRequests.set(id, entry);
    try {
      await task;
    } finally {
      if (answerRequests.get(id) === entry) answerRequests.delete(id);
    }
  }

  async function loadActivity(id: string): Promise<void> {
    const ctx = context(),
      view = runs[id];
    if (
      !ctx ||
      !view ||
      view.activityLoading ||
      view.activityLoaded ||
      view.replay
    )
      return;
    if (terminal(view.projection.runStatus)) {
      view.activityLoaded = true;
      return;
    }
    view.activityLoading = true;
    view.activityError = "";
    try {
      let projection = createRunProjection({
        threadId: ctx.threadId,
        runId: id,
        assistantMessageId: view.run.assistant_message_id,
      });
      while (!terminal(projection.runStatus)) {
        const page = await authorized(
          ctx,
          `${runPath(ctx, id)}/events?after=${projection.cursor}&limit=100`,
        );
        if (!current(ctx)) return;
        if (
          !isRecord(page) ||
          !Array.isArray(page.items) ||
          !page.items.length ||
          page.items.length > 100
        )
          throw new EventProtocolError();
        for (const raw of page.items) {
          const result = reduceRunEvent(projection, parseRunEvent(raw));
          if (result.kind !== "applied") throw new EventProtocolError();
          projection = result.state;
        }
        if (page.next_cursor !== projection.cursor)
          throw new EventProtocolError();
      }
      const message = messages.value.find(
        (m) => m.id === view.run.assistant_message_id,
      );
      if (
        projection.runStatus !== view.run.status ||
        (message && projection.content !== message.content)
      )
        throw new EventProtocolError();
      view.projection = projection;
      view.activityLoaded = true;
    } catch {
      if (current(ctx)) view.activityError = "执行过程暂时无法读取，请重试。";
    } finally {
      if (current(ctx)) view.activityLoading = false;
    }
  }
  function runStatus(view: RunView): string {
    return terminal(view.run.status)
      ? view.run.status
      : view.projection.cursor
        ? view.projection.runStatus
        : view.run.status;
  }
  function statusLabel(id: string): string {
    const view = runs[id];
    if (!view) return "正在确认状态";
    const status = runStatus(view);
    if (status === "succeeded") return "已完成";
    if (status === "failed") return "回答未完成";
    if (status === "cancelled") return "已取消";
    if (
      view.cancelBusy ||
      view.run.cancel_requested_at ||
      view.projection.cancelRequestedAt
    )
      return "取消中";
    if (status === "waiting_input")
      return view.resumeAccepted ? "补充已接收，等待继续" : "等待你补充信息";
    if (status === "resume_pending") return "补充已接收，等待继续";
    if (view.projection.messageStatus !== "pending") return "正在确认执行结果";
    if (view.projection.tools.some((t) => t.status === "running"))
      return "正在调用工具";
    if (view.projection.tools.some((t) => t.status === "completed"))
      return "正在组织回答";
    return status === "pending" ? "排队中" : "生成中";
  }
  function failureInfo(id: string) {
    const view = runs[id];
    return view ? failureForRun(view.run, view.projection) : null;
  }
  const displayMessages = computed(() =>
    messages.value.map((message) => {
      const projection = runs[message.run_id]?.projection;
      if (
        message.role !== "assistant" ||
        !projection ||
        projection.cursor === 0 ||
        !["pending", "waiting_input"].includes(message.status)
      )
        return message;
      return {
        ...message,
        content: projection.content,
        status: projection.messageStatus,
      };
    }),
  );

  return {
    threadId,
    messages,
    runs,
    answers,
    historyLoading,
    historyLoaded,
    historyError,
    previousCursor,
    draft,
    skills,
    selectedSkill,
    skillsLoading,
    skillsError,
    loadSkills,
    pending,
    submitting,
    submitError,
    acceptedRunId,
    displayMessages,
    open,
    close,
    loadHistory,
    submit,
    discardPending,
    cancel,
    resumeRun,
    synchronizeRun,
    retryRun,
    loadAnswer,
    loadActivity,
    runStatus,
    statusLabel,
    failureInfo,
  };
});
