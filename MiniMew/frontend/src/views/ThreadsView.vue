<script setup lang="ts">
import { computed, onUnmounted, watch } from "vue";
import { useRoute } from "vue-router";
import ChatPanel from "../components/ChatPanel.vue";
import NewChatPanel from "../components/NewChatPanel.vue";
import { useThreadsStore } from "../stores/threads";
import { scopeKeyFor, useWorkspaceStore } from "../stores/workspace";

const route = useRoute();
const threads = useThreadsStore();
const workspace = useWorkspaceStore();

async function toggleArchive() {
  if (
    threads.selected &&
    (await workspace.archiveThread(
      threads.selected,
      !threads.selected.archived,
    ))
  )
    await threads.selectThread(id.value);
}

const projectId = computed(() =>
  typeof route.query.project === "string" ? route.query.project : null,
);
const id = computed(() =>
  typeof route.params.threadId === "string" ? route.params.threadId : "",
);

threads.setProject(projectId.value);

watch(projectId, (value) => {
  threads.setProject(value);
  if (id.value) void threads.selectThread(id.value);
});

watch(
  id,
  (value) =>
    value ? void threads.selectThread(value) : threads.clearSelection(),
  { immediate: true },
);

onUnmounted(() => threads.clearSelection());

const title = computed(() => {
  const bucket = workspace.ensureBucket(scopeKeyFor(projectId.value));
  const selectedId = threads.selected?.id;
  return (
    bucket.items.find((thread) => thread.id === selectedId)?.title ??
    threads.items.find((thread) => thread.id === selectedId)?.title ??
    threads.selected?.title
  );
});

const scopeLabel = computed(() =>
  projectId.value
    ? `项目 · ${workspace.projectName(projectId.value)}`
    : "个人空间",
);
</script>

<template>
  <section class="thread-stage" aria-label="当前对话">
    <NewChatPanel v-if="!id" :project-id="projectId" />

    <div v-else-if="threads.detailLoading" class="thread-empty">
      <span class="spinner" aria-hidden="true"></span>
      <h2>正在打开对话</h2>
    </div>

    <div v-else-if="threads.unavailable" class="thread-empty">
      <span class="eyebrow">无法打开</span>
      <h2>对话不存在或不可访问</h2>
      <p>请从左侧选择其他对话，或开始一段新会话。</p>
      <RouterLink
        class="text-link"
        :to="
          projectId
            ? { path: '/threads', query: { project: projectId } }
            : '/threads'
        "
      >
        返回新会话 →
      </RouterLink>
    </div>

    <div v-else-if="threads.detailError" class="thread-empty">
      <h2>暂时无法打开对话</h2>
      <p class="error" role="alert">{{ threads.detailError }}</p>
      <button class="secondary" @click="threads.selectThread(id)">
        重试打开对话
      </button>
    </div>

    <div v-else-if="threads.selected" class="thread-active">
      <header class="thread-heading">
        <div class="thread-heading-left">
          <h2>{{ title || "未命名对话" }}</h2>
          <span class="scope-badge">{{ scopeLabel }}</span>
        </div>
        <div class="thread-heading-actions">
          <button
            class="quiet-button"
            :disabled="workspace.archiveBusy"
            @click="toggleArchive"
          >
            {{ threads.selected.archived ? "恢复会话" : "归档会话" }}
          </button>
          <RouterLink
            v-if="projectId"
            class="quiet-link"
            :to="`/projects/${projectId}`"
          >
            项目概览
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
      </header>
      <p v-if="workspace.archiveError" class="thread-error-banner" role="alert">
        {{ workspace.archiveError }}
      </p>
      <p
        v-if="threads.selected.archived"
        class="thread-archived-banner"
        role="status"
      >
        此会话已归档，历史记录保留。
      </p>
      <ChatPanel :key="threads.selected.id" :thread-id="threads.selected.id" />
    </div>
  </section>
</template>
