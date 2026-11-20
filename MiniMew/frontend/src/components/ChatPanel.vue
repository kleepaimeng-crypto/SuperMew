<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { useChatStore } from "../stores/chat";
import { useThreadsStore } from "../stores/threads";
import { useWorkspaceStore } from "../stores/workspace";
import { terminal } from "../chat";
import AnswerBody from "./AnswerBody.vue";
import ToolActivity from "./ToolActivity.vue";
import RunFailure from "./RunFailure.vue";

const props = defineProps<{ threadId: string }>();
const chat = useChatStore();
const threads = useThreadsStore();
const workspace = useWorkspaceStore();
const feed = ref<HTMLElement | null>(null);
const nearEnd = ref(true);
watch(
  () => props.threadId,
  async (id) => {
    await chat.open(id);
    if (chat.threadId === id) void chat.loadSkills();
  },
  { immediate: true },
);
onUnmounted(() => chat.close());
const acceptedMissing = computed(
  () =>
    chat.acceptedRunId &&
    !chat.messages.some((message) => message.run_id === chat.acceptedRunId),
);
watch(
  () =>
    chat.displayMessages
      .map(
        (message) =>
          `${message.id}:${message.content}:${message.status}:${JSON.stringify(liveSteps(message.run_id))}`,
      )
      .join("\n"),
  followFeed,
);
function liveSteps(id: string) {
  const view = chat.runs[id];
  return view && !terminal(chat.runStatus(view))
    ? [view.projection.steps, view.projection.stepDetails]
    : [];
}
async function followFeed() {
  if (!nearEnd.value) return;
  await nextTick();
  if (feed.value) feed.value.scrollTop = feed.value.scrollHeight;
}

async function send() {
  nearEnd.value = true;
  if (await chat.submit()) {
    void threads.loadList(true);
    void workspace.loadScopeThreads(threads.projectId, true);
  }
}
function enter(event: KeyboardEvent) {
  if (!event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void send();
  }
}
</script>

<template>
  <div class="chat-panel">
    <div class="chat-toolbar">
      <span role="status">{{
        chat.historyLoading ? "正在同步消息…" : "消息记录"
      }}</span>
      <button
        class="quiet-button"
        :disabled="chat.historyLoading"
        @click="chat.loadHistory()"
      >
        同步历史
      </button>
    </div>
    <div v-if="chat.historyError" class="chat-notice" role="alert">
      <span>{{ chat.historyError }}</span>
      <button
        class="quiet-button"
        :disabled="chat.historyLoading"
        @click="chat.loadHistory()"
      >
        重试同步历史
      </button>
    </div>
    <div
      ref="feed"
      class="message-feed"
      aria-label="消息记录"
      tabindex="0"
      @scroll="
        nearEnd =
          !feed || feed.scrollHeight - feed.scrollTop - feed.clientHeight < 100
      "
    >
      <button
        v-if="chat.previousCursor !== null"
        class="load-more"
        :disabled="chat.historyLoading"
        @click="
          nearEnd = false;
          chat.loadHistory(true);
        "
      >
        加载更早消息
      </button>
      <div
        v-if="
          chat.historyLoaded && !chat.displayMessages.length && !acceptedMissing
        "
        class="chat-empty"
      >
        <h3>开始这段对话</h3>
        <p>写下你的问题，回复会保存在这段对话里。</p>
      </div>
      <article
        v-for="message in chat.displayMessages"
        :key="message.id"
        class="chat-message"
        :class="{ 'user-message': message.role === 'user' }"
        :aria-label="message.role === 'user' ? '你的消息' : 'MiniMew 回复'"
      >
        <div class="message-author">
          {{ message.role === "user" ? "你" : "MiniMew" }}
        </div>
        <p
          v-if="message.role === 'assistant' && chat.runs[message.run_id]"
          class="message-skill"
        >
          任务模式：{{ chat.runs[message.run_id]!.run.skill?.name ?? "未记录" }}
          <template v-if="chat.runs[message.run_id]!.run.skill">
            · v{{ chat.runs[message.run_id]!.run.skill!.version }}</template
          >
        </p>
        <ToolActivity
          v-if="message.role === 'assistant' && chat.runs[message.run_id]"
          :tools="chat.runs[message.run_id]!.projection.tools"
          :steps="chat.runs[message.run_id]!.projection.steps"
          :step-details="chat.runs[message.run_id]!.projection.stepDetails"
          :run-status="
            chat.runs[message.run_id]!.projection.cursor
              ? chat.runs[message.run_id]!.projection.runStatus
              : chat.runs[message.run_id]!.run.status
          "
          :loading="chat.runs[message.run_id]!.activityLoading"
          :loaded="chat.runs[message.run_id]!.activityLoaded"
          :error="chat.runs[message.run_id]!.activityError"
          @load="chat.loadActivity(message.run_id)"
          @layout="followFeed"
        />
        <AnswerBody
          v-if="
            message.role === 'assistant' &&
            (message.status !== 'pending' ||
              !chat.runs[message.run_id]?.projection.steps.length)
          "
          :message="message"
          :answer="chat.answers[message.run_id]"
          @retry="chat.loadAnswer(message.run_id, true)"
          @refresh-source="chat.loadAnswer(message.run_id, true)"
        />
        <div v-if="message.role === 'user'" class="message-body">
          {{ message.content }}
        </div>
        <template v-if="message.role === 'assistant'">
          <RunFailure
            :failure="chat.failureInfo(message.run_id)"
            :run-id="message.run_id"
          />
          <form
            v-if="
              chat.runs[message.run_id]?.projection.runStatus ===
                'waiting_input' && !chat.runs[message.run_id]!.resumeAccepted
            "
            class="hitl-form"
            @submit.prevent="chat.resumeRun(message.run_id)"
          >
            <label :for="`clarification-${message.run_id}`">{{
              chat.runs[message.run_id]!.projection.waiting?.prompt
            }}</label>
            <p class="hitl-help">
              补充后继续这次回答。等待期间可以离开或刷新页面，最迟请在
              {{
                new Date(
                  chat.runs[message.run_id]!.projection.waiting!.expiresAt,
                ).toLocaleTimeString()
              }}
              前提交。
            </p>
            <textarea
              :id="`clarification-${message.run_id}`"
              v-model="chat.runs[message.run_id]!.resumeDraft"
              maxlength="1024"
              rows="2"
              required
              :readonly="
                chat.runs[message.run_id]!.resumeBusy ||
                chat.runs[message.run_id]!.resumePending
              "
            />
            <button
              type="submit"
              :disabled="chat.runs[message.run_id]!.resumeBusy"
            >
              {{
                chat.runs[message.run_id]!.resumeBusy
                  ? "正在提交补充…"
                  : chat.runs[message.run_id]!.resumePending
                    ? "重试原补充"
                    : "补充并继续"
              }}
            </button>
            <p
              v-if="chat.runs[message.run_id]!.resumeError"
              class="error"
              role="alert"
            >
              {{ chat.runs[message.run_id]!.resumeError }}
            </p>
          </form>
          <div class="message-meta">
            <span role="status">{{ chat.statusLabel(message.run_id) }}</span>
            <button
              v-if="
                chat.runs[message.run_id] &&
                !terminal(chat.runStatus(chat.runs[message.run_id]!))
              "
              class="quiet-button"
              :disabled="chat.runs[message.run_id]!.cancelBusy"
              @click="chat.cancel(message.run_id)"
            >
              停止回复
            </button>
          </div>
          <div
            v-if="chat.runs[message.run_id]?.error"
            class="message-recovery"
            role="status"
          >
            <span>{{ chat.runs[message.run_id]!.error }}</span>
            <button
              v-if="chat.runs[message.run_id]!.connection === 'error'"
              class="quiet-button"
              @click="chat.retryRun(message.run_id)"
            >
              重试连接
            </button>
          </div>
          <div
            v-if="chat.runs[message.run_id]?.cancelError"
            class="message-recovery"
            role="alert"
          >
            <span>{{ chat.runs[message.run_id]!.cancelError }}</span>
            <button
              class="quiet-button"
              @click="chat.synchronizeRun(message.run_id)"
            >
              查询状态
            </button>
          </div>
        </template>
      </article>
      <div v-if="acceptedMissing" class="chat-notice" role="status">
        <RunFailure
          :failure="chat.failureInfo(chat.acceptedRunId!)"
          :run-id="chat.acceptedRunId!"
        />
        <span
          >问题已提交，消息记录尚未同步。{{
            chat.statusLabel(chat.acceptedRunId!)
          }}</span
        >
        <button
          v-if="
            chat.runs[chat.acceptedRunId!] &&
            !terminal(chat.runStatus(chat.runs[chat.acceptedRunId!]!))
          "
          class="quiet-button"
          @click="chat.cancel(chat.acceptedRunId!)"
        >
          停止回复
        </button>
      </div>
    </div>
    <form class="chat-composer" @submit.prevent="send">
      <div class="skill-choice">
        <label for="chat-skill">任务模式</label>
        <select
          id="chat-skill"
          v-model="chat.selectedSkill"
          :disabled="!!chat.pending || chat.submitting || chat.skillsLoading"
          aria-describedby="skill-help"
        >
          <option value="">默认助手</option>
          <option
            v-if="
              chat.pending?.skill &&
              !chat.skills.some(
                (s) =>
                  s.id === chat.pending!.skill!.id &&
                  s.version === chat.pending!.skill!.version,
              )
            "
            :value="`${chat.pending.skill.id}@${chat.pending.skill.version}`"
          >
            {{ chat.pending.skillName || chat.pending.skill.id }} · v{{
              chat.pending.skill.version
            }}（待确认）
          </option>
          <option
            v-for="skill in chat.skills.filter((s) => s.id !== 'default')"
            :key="`${skill.id}@${skill.version}`"
            :value="`${skill.id}@${skill.version}`"
          >
            {{ skill.name }} · v{{ skill.version }}
          </option>
        </select>
        <span id="skill-help">{{
          chat.pending
            ? "待确认的提交将保留原任务模式。"
            : chat.skillsLoading
              ? "正在加载任务模式…"
              : "用于下一次提问，已有任务保持原模式。"
        }}</span>
      </div>
      <div v-if="chat.skillsError" class="skill-error" role="status">
        {{ chat.skillsError }}
        <button type="button" class="quiet-button" @click="chat.loadSkills()">
          重试加载模式
        </button>
      </div>
      <label for="chat-question">你的问题</label>
      <textarea
        id="chat-question"
        v-model="chat.draft"
        rows="3"
        maxlength="20000"
        :readonly="!!chat.pending || chat.submitting"
        placeholder="输入问题，Enter 发送，Shift + Enter 换行"
        aria-describedby="chat-compose-help"
        @keydown.enter="enter"
      />
      <p v-if="chat.submitError" class="error" role="alert">
        {{ chat.submitError }}
      </p>
      <div class="composer-actions">
        <span id="chat-compose-help">{{
          chat.pending
            ? "待确认的提交会保留原正文和原请求标识。"
            : "回答会自动保存；关闭页面不会停止任务。"
        }}</span>
        <button
          v-if="chat.pending && !chat.submitting"
          type="button"
          class="quiet-button"
          @click="chat.discardPending()"
        >
          放弃本地草稿
        </button>
        <button
          type="submit"
          class="primary"
          :disabled="chat.submitting || (!chat.pending && !chat.draft.trim())"
        >
          {{
            chat.submitting
              ? "正在提交…"
              : chat.pending
                ? "重试原提交"
                : "发送问题"
          }}
        </button>
      </div>
      <p v-if="chat.pending" class="composer-hint">
        放弃草稿只清理本地内容；未确认的请求可能已经保存，可先同步历史检查。
      </p>
    </form>
  </div>
</template>

<style scoped>
.skill-choice {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.skill-choice label {
  margin: 0;
}
.skill-choice select {
  width: auto;
  max-width: 100%;
  padding: 8px 10px;
  border: 1px solid #b3c0ae;
  border-radius: 6px;
  background: #fcfdf9;
  color: #294e39;
  font: inherit;
}
.skill-choice span,
.message-skill,
.skill-error {
  font-size: 12px;
  line-height: 1.6;
  color: #536552;
}
.message-skill {
  margin: 4px 0 8px;
}
.skill-error {
  margin-bottom: 10px;
}
@media (pointer: coarse) {
  .skill-choice select {
    min-height: 44px;
  }
}
</style>
