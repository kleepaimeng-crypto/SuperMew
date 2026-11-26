import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { ApiError, request } from "../api";
import { randomUuid } from "../uuid";
import { parseRun } from "../chat";
import { isRecord } from "../events/runEventContract";
import {
  parseSelection,
  parseSkills,
  type SkillSelection,
  type SkillSummary,
} from "../skills";
import { parseThread } from "../threads";
import { useAuthStore } from "./auth";
import { scopeKeyFor, useWorkspaceStore } from "./workspace";

interface NewChatIntent {
  key: string;
  content: string;
  skill?: SkillSelection;
  skillName?: string;
}

const newChatPrefix = "minimew.new-chat-pending:";
const chatPendingPrefix = "minimew.chat-pending:";

export const useNewChatStore = defineStore("newChat", () => {
  const auth = useAuthStore();
  const workspace = useWorkspaceStore();

  const scopeProjectId = ref<string | null>(null);
  const draft = ref("");
  const skills = ref<SkillSummary[]>([]);
  const selectedSkill = ref("");
  const skillsLoading = ref(false);
  const skillsError = ref("");
  const pending = ref<NewChatIntent | null>(null);
  const submitting = ref(false);
  const submitError = ref("");

  let epoch = 0;
  let controller = new AbortController();

  function storageKey(userId: string, projectId: string | null) {
    return `${newChatPrefix}${userId}:${scopeKeyFor(projectId)}`;
  }

  function saveIntent(projectId: string | null, value: NewChatIntent | null) {
    if (!auth.user) return;
    try {
      const key = storageKey(auth.user.id, projectId);
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    } catch {
      /* 存储禁用时保留内存草稿。 */
    }
  }

  function saveThreadRunIntent(
    userId: string,
    threadId: string,
    value: NewChatIntent | null,
  ) {
    try {
      const key = `${chatPendingPrefix}${userId}:${threadId}`;
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    } catch {
      /* 存储禁用时由后续流程处理。 */
    }
  }

  function restoreIntent(
    userId: string,
    projectId: string | null,
  ): NewChatIntent | null {
    try {
      const raw = sessionStorage.getItem(storageKey(userId, projectId));
      const value: unknown = raw ? JSON.parse(raw) : null;
      if (
        isRecord(value) &&
        typeof value.key === "string" &&
        /^[\w.:-]{1,128}$/.test(value.key) &&
        typeof value.content === "string" &&
        value.content.trim() &&
        [...value.content].length <= 20000
      ) {
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
      }
    } catch {
      /* 无可恢复的新会话草稿。 */
    }
    return null;
  }

  function clearAllStorage(prefix: string) {
    try {
      Object.keys(sessionStorage)
        .filter((key) => key.startsWith(prefix))
        .forEach((key) => sessionStorage.removeItem(key));
    } catch {
      /* 忽略存储清理异常。 */
    }
  }

  function reset() {
    ++epoch;
    controller.abort();
    controller = new AbortController();
    draft.value = "";
    pending.value = null;
    submitting.value = false;
    submitError.value = "";
    skills.value = [];
    selectedSkill.value = "";
    skillsLoading.value = false;
    skillsError.value = "";
  }

  watch(
    () => auth.user?.id,
    (next, previous) => {
      if (next === previous) return;
      reset();
      if (previous && (next || auth.logoutPending)) {
        clearAllStorage(`${newChatPrefix}${previous}:`);
      }
    },
    { flush: "sync" },
  );

  watch(
    () => auth.status,
    (status) => {
      if (status !== "anonymous") return;
      clearAllStorage(newChatPrefix);
    },
    { flush: "sync" },
  );

  function context() {
    if (auth.status !== "authenticated" || !auth.accessToken || !auth.user)
      return null;
    return {
      epoch,
      userId: auth.user.id,
      projectId: scopeProjectId.value,
      signal: controller.signal,
    };
  }

  type Context = NonNullable<ReturnType<typeof context>>;

  function current(ctx: Context) {
    return (
      ctx.epoch === epoch &&
      ctx.userId === auth.user?.id &&
      ctx.projectId === scopeProjectId.value &&
      !ctx.signal.aborted
    );
  }

  async function authorized(
    ctx: Context,
    path: string,
    method = "GET",
    body?: unknown,
  ) {
    if (!current(ctx) || !auth.accessToken)
      throw new DOMException("Aborted", "AbortError");
    const token = auth.accessToken;
    try {
      return await request(path, method, body, token, ctx.signal);
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 401) throw cause;
      if (
        !(await auth.refreshAccess(token)) ||
        !current(ctx) ||
        !auth.accessToken
      )
        throw cause;
      return request(path, method, body, auth.accessToken, ctx.signal);
    }
  }

  function openScope(projectId: string | null) {
    if (scopeProjectId.value !== projectId) {
      ++epoch;
      controller.abort();
      controller = new AbortController();
      scopeProjectId.value = projectId;
      submitting.value = false;
      submitError.value = "";
      draft.value = "";
      selectedSkill.value = "";
      pending.value = null;
    }
    if (auth.user) {
      const restored = restoreIntent(auth.user.id, projectId);
      if (restored) {
        pending.value = restored;
        draft.value = restored.content;
        if (restored.skill) {
          selectedSkill.value = `${restored.skill.id}@${restored.skill.version}`;
        }
        submitError.value =
          "上次创建对话结果未确认，请先检查左侧对话列表，再决定是否重试发送。";
      }
    }
    if (!skills.value.length && !skillsLoading.value) {
      void loadSkills();
    }
  }

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

  async function submitFirstMessage(): Promise<{
    threadId: string;
    submitted: boolean;
  } | null> {
    const ctx = context();
    if (
      !ctx ||
      submitting.value ||
      (!pending.value &&
        (!draft.value.trim() ||
          [...draft.value].length > 20000 ||
          draft.value.includes("\0")))
    )
      return null;

    if (!pending.value) {
      const chosen = skills.value.find(
        (s) => `${s.id}@${s.version}` === selectedSkill.value,
      );
      if (selectedSkill.value && !chosen) {
        submitError.value = "所选任务模式不可用，请重新加载或选择默认助手。";
        return null;
      }
      pending.value = {
        key: randomUuid(),
        content: draft.value,
        ...(chosen
          ? {
              skill: { id: chosen.id, version: chosen.version },
              skillName: chosen.name,
            }
          : {}),
      };
    }

    const intent: NewChatIntent = { ...pending.value };
    saveIntent(ctx.projectId, intent);
    submitting.value = true;
    submitError.value = "";

    let createdThreadId: string;
    try {
      const thread = parseThread(
        await authorized(
          ctx,
          "/v1/threads",
          "POST",
          ctx.projectId ? { project_id: ctx.projectId } : {},
        ),
      );
      if (!current(ctx)) return null;
      createdThreadId = thread.id;
      workspace.upsertThread(thread);
      // 阶段 A 已确认创建 Thread：立即把 Run 提交意图转交给该 Thread 的幂等恢复键，
      // 确保阶段 B 若失败或刷新时不会重复创建新 Thread。
      saveThreadRunIntent(ctx.userId, createdThreadId, intent);
      saveIntent(ctx.projectId, null);
    } catch (cause) {
      if (!current(ctx)) return null;
      submitError.value =
        cause instanceof ApiError && [403, 404, 422].includes(cause.status)
          ? "当前工作区不可用，请刷新项目列表后再试。"
          : "创建对话结果未确认，请先检查左侧对话列表，再决定是否重试发送。";
      submitting.value = false;
      return null;
    }

    try {
      parseRun(
        await authorized(ctx, `/v1/threads/${createdThreadId}/runs`, "POST", {
          idempotency_key: intent.key,
          content: intent.content,
          ...(intent.skill ? { skill: intent.skill } : {}),
        }),
        createdThreadId,
      );
      if (!current(ctx)) return null;
      saveThreadRunIntent(ctx.userId, createdThreadId, null);
      pending.value = null;
      draft.value = "";
      submitError.value = "";
      void workspace.loadScopeThreads(ctx.projectId, true);
      return { threadId: createdThreadId, submitted: true };
    } catch {
      if (!current(ctx)) return null;
      // Thread 已创建，原 Run 提交意图已保存在 chat-pending:<user>:<threadId>，
      // 交由目标对话页直接恢复并允许用户“重试原提交”，绝不重复创建 Thread。
      pending.value = null;
      draft.value = "";
      submitError.value = "";
      void workspace.loadScopeThreads(ctx.projectId, true);
      return { threadId: createdThreadId, submitted: false };
    } finally {
      if (current(ctx)) submitting.value = false;
    }
  }

  function discardPending() {
    if (submitting.value) return;
    pending.value = null;
    saveIntent(scopeProjectId.value, null);
    submitError.value = "";
  }

  return {
    scopeProjectId,
    draft,
    skills,
    selectedSkill,
    skillsLoading,
    skillsError,
    pending,
    submitting,
    submitError,
    openScope,
    loadSkills,
    submitFirstMessage,
    discardPending,
    reset,
  };
});
