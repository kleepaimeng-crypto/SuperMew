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

      <form class="composer-card" @submit.prevent="send">
        <label class="sr-only-label" for="new-chat-question">你的问题</label>
        <textarea
          id="new-chat-question"
          v-model="newChat.draft"
          rows="3"
          maxlength="20000"
          :readonly="!!newChat.pending || newChat.submitting"
          placeholder="输入问题，Enter 发送，Shift + Enter 换行"
          aria-describedby="new-chat-compose-help"
          @keydown.enter="onEnter"
        />

        <p
          v-if="newChat.submitError"
          class="composer-inline-error"
          role="alert"
        >
          {{ newChat.submitError }}
        </p>

        <div class="composer-bar">
          <div class="composer-bar-left">
            <label class="sr-only-label" for="new-chat-skill">任务模式</label>
            <select
              id="new-chat-skill"
              v-model="newChat.selectedSkill"
              class="composer-skill-select"
              :disabled="
                !!newChat.pending || newChat.submitting || newChat.skillsLoading
              "
              aria-label="任务模式"
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
                {{ newChat.pending.skillName || newChat.pending.skill.id }} ·
                v{{ newChat.pending.skill.version }}（待确认）
              </option>
              <option
                v-for="skill in newChat.skills.filter(
                  (s) => s.id !== 'default',
                )"
                :key="`${skill.id}@${skill.version}`"
                :value="`${skill.id}@${skill.version}`"
              >
                {{ skill.name }} · v{{ skill.version }}
              </option>
            </select>
            <span
              v-if="newChat.skillsError"
              class="composer-skill-error"
              role="status"
            >
              {{ newChat.skillsError }}
              <button
                type="button"
                class="quiet-button"
                @click="newChat.loadSkills()"
              >
                重试加载模式
              </button>
            </span>
          </div>

          <div class="composer-bar-right">
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
              class="primary composer-send-btn"
              :disabled="
                newChat.submitting ||
                (!newChat.pending && !newChat.draft.trim())
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
        </div>
      </form>

      <p id="new-chat-compose-help" class="composer-footer-note">
        {{
          newChat.pending
            ? "草稿已保留，避免重复创建前可先检查左侧会话列表。"
            : "空白草稿不会创建空对话；回答会自动保存，关闭页面不会停止任务。"
        }}
      </p>
    </div>
  </section>
</template>
