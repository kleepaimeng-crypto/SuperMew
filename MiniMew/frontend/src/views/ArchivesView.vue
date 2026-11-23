<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute } from "vue-router";
import { scopeKeyFor, useWorkspaceStore } from "../stores/workspace";

const route = useRoute(),
  workspace = useWorkspaceStore();

const project = computed(() =>
  typeof route.query.project === "string" ? route.query.project : null,
);
const bucket = computed(() =>
  workspace.ensureBucket(scopeKeyFor(project.value) + ":archived"),
);

watch(project, (value) => void workspace.loadScopeThreads(value, true, true), {
  immediate: true,
});

function formatDate(iso: string) {
  return new Date(iso).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
</script>

<template>
  <section class="page-container" aria-label="已归档聊天">
    <header class="page-header">
      <div class="page-header-left">
        <div class="page-header-title-row">
          <h1>已归档聊天</h1>
          <span class="scope-badge">{{ workspace.projectName(project) }}</span>
        </div>
        <p class="page-header-sub">
          {{ workspace.projectName(project) }} ·
          归档仅从侧边栏隐藏，所有消息与历史记录均完整保留，随时可恢复。
        </p>
      </div>
      <div class="page-header-actions">
        <button
          class="secondary"
          :disabled="bucket.loading"
          @click="workspace.loadScopeThreads(project, true, true)"
        >
          刷新
        </button>
        <RouterLink
          class="btn-link primary"
          :to="{ path: '/threads', query: project ? { project } : {} }"
        >
          返回当前空间
        </RouterLink>
      </div>
    </header>

    <p v-if="bucket.error || workspace.archiveError" class="error" role="alert">
      {{ bucket.error || workspace.archiveError }}
    </p>

    <p
      v-if="bucket.loading && !bucket.items.length"
      class="muted"
      role="status"
    >
      正在加载…
    </p>

    <div
      v-else-if="bucket.loaded && !bucket.items.length && !bucket.error"
      class="empty-state-card"
    >
      <h2>当前空间没有已归档聊天。</h2>
      <p>
        在任意对话顶栏点击「归档会话」后，记录会收进这里，不再占用左侧会话列表。
      </p>
      <div class="empty-state-actions">
        <RouterLink
          class="btn-link secondary"
          :to="{ path: '/threads', query: project ? { project } : {} }"
        >
          进入对话
        </RouterLink>
      </div>
    </div>

    <div v-else-if="bucket.items.length" class="panel-card archive-panel">
      <ul class="archive-list">
        <li v-for="thread in bucket.items" :key="thread.id" class="archive-row">
          <div class="archive-row-main">
            <RouterLink
              class="archive-title"
              :to="{
                path: `/threads/${thread.id}`,
                query: project ? { project } : {},
              }"
            >
              {{ thread.title || "未命名对话" }}
            </RouterLink>
            <span class="archive-meta">
              创建于 {{ formatDate(thread.created_at) }}
            </span>
          </div>
          <div class="archive-row-actions">
            <RouterLink
              class="btn-link secondary"
              :to="{
                path: `/threads/${thread.id}`,
                query: project ? { project } : {},
              }"
            >
              查看记录
            </RouterLink>
            <button
              class="primary"
              :disabled="workspace.archiveBusy"
              @click="workspace.archiveThread(thread, false)"
            >
              恢复会话
            </button>
          </div>
        </li>
      </ul>

      <button
        v-if="bucket.nextOffset !== null"
        class="secondary archive-load-more"
        :disabled="bucket.loading"
        @click="workspace.loadScopeThreads(project, false, true)"
      >
        {{ bucket.loading ? "正在加载…" : "加载更多" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.archive-panel {
  padding: 8px 24px;
}

.archive-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.archive-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 14px;
  padding: 16px 0;
  border-bottom: 1px solid var(--border);
}

.archive-row:last-child {
  border-bottom: 0;
}

.archive-row-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.archive-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.archive-title:hover {
  color: var(--accent);
}

.archive-meta {
  font-size: 12px;
  color: var(--ink-muted);
}

.archive-row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.archive-load-more {
  margin: 16px 0 8px;
}
</style>
