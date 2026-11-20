<script setup lang="ts">
import { computed, watch } from "vue";
import { useRouter } from "vue-router";
import { useNewChatStore } from "../stores/newChat";
import { useWorkspaceStore } from "../stores/workspace";

const props = defineProps<{ projectId: string | null }>();
const router = useRouter();
const newChat = useNewChatStore();
const workspace = useWorkspaceStore();

watch(
  () => props.projectId,
  (id) => {
    newChat.openScope(id);
  },
  { immediate: true },
);

const scopeLabel = computed(() =>
  props.projectId
    ? `项目 · ${workspace.projectName(props.projectId)}`
    : "个人空间",
);

async function send() {
  const result = await newChat.submitFirstMessage();
  if (!result) return;
  await router.replace({
    path: `/threads/${result.threadId}`,
    query: props.projectId ? { project: props.projectId } : {},
  });
}

function onEnter(event: KeyboardEvent) {
  if (!event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void send();
  }
}
</script>

<template>
  <section class="new-chat-stage" aria-label="新会话">
    <div class="new-chat-card">
      <header class="new-chat-header">
        <div class="new-chat-scope-row">
          <span class="scope-badge">{{ scopeLabel }}</span>
          <RouterLink
            v-if="projectId"
            class="quiet-link"
            :to="`/projects/${projectId}`"
          >
            项目概览与记忆
          </RouterLink>
          <RouterLink
            class="quiet-link"
            :to="
              projectId
                ? { path: '/documents', query: { project: projectId } }
                : '/documents'
            "
          >
            {{ projectId ? "项目文档" : "个人文档" }}
          </RouterLink>
        </div>
        <h1>开始新会话</h1>
        <p>
          {{
            projectId
              ? "当前提问将归属此项目，并使用该项目的知识库与已启用记忆。"
              : "输入你的问题，发送首条消息时才会创建对话并保存记录。"
          }}
        </p>
      </header>

      <form class="chat-composer new-chat-composer" @submit.prevent="send">
        <div class="skill-choice">
          <label for="new-chat-skill">任务模式</label>
          <select
            id="new-chat-skill"
            v-model="newChat.selectedSkill"
            :disabled="
              !!newChat.pending || newChat.submitting || newChat.skillsLoading
            "
            aria-describedby="new-chat-skill-help"
          >
            <option value="">默认助手</option>
            <option
              v-if="
                newChat.pending?.skill &&
                !newChat.skills.some(
                  (s) =>
                    s.id === newChat.pending!.skill!.id &&
                    s.version === newChat.pending!.skill!.version,
                )
              "
              :value="`${newChat.pending.skill.id}@${newChat.pending.skill.version}`"
            >
              {{ newChat.pending.skillName || newChat.pending.skill.id }} · v{{
                newChat.pending.skill.version
              }}（待确认）
            </option>
            <option
              v-for="skill in newChat.skills.filter((s) => s.id !== 'default')"
              :key="`${skill.id}@${skill.version}`"
              :value="`${skill.id}@${skill.version}`"
            >
              {{ skill.name }} · v{{ skill.version }}
            </option>
          </select>
          <span id="new-chat-skill-help">{{
            newChat.pending
              ? "待确认的提交将保留原任务模式。"
              : newChat.skillsLoading
                ? "正在加载任务模式…"
                : "用于本次提问，发送后可在对话中继续切换。"
          }}</span>
        </div>

        <div v-if="newChat.skillsError" class="skill-error" role="status">
          {{ newChat.skillsError }}
          <button
            type="button"
            class="quiet-button"
            @click="newChat.loadSkills()"
          >
            重试加载模式
          </button>
        </div>

        <label for="new-chat-question">你的问题</label>
        <textarea
          id="new-chat-question"
          v-model="newChat.draft"
          rows="4"
          maxlength="20000"
          :readonly="!!newChat.pending || newChat.submitting"
          placeholder="输入问题，Enter 发送，Shift + Enter 换行"
          aria-describedby="new-chat-compose-help"
          @keydown.enter="onEnter"
        />

        <p v-if="newChat.submitError" class="error" role="alert">
          {{ newChat.submitError }}
        </p>

        <div class="composer-actions">
          <span id="new-chat-compose-help">{{
            newChat.pending
              ? "草稿已保留，避免重复创建前可先检查左侧会话列表。"
              : "空白草稿不会创建空对话；回答会自动保存。"
          }}</span>
          <button
            v-if="newChat.pending && !newChat.submitting"
            type="button"
            class="quiet-button"
            @click="newChat.discardPending()"
          >
            放弃本地草稿
          </button>
          <button
            type="submit"
            class="primary"
            :disabled="
              newChat.submitting || (!newChat.pending && !newChat.draft.trim())
            "
          >
            {{
              newChat.submitting
                ? "正在发送…"
                : newChat.pending
                  ? "重试发送"
                  : "发送问题"
            }}
          </button>
        </div>
      </form>
    </div>
  </section>
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
.skill-error {
  font-size: 12px;
  line-height: 1.6;
  color: #536552;
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
