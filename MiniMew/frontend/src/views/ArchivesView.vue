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
</script>
<template>
  <section class="page-container" aria-label="已归档聊天">
    <header class="page-header">
      <div>
        <h1>已归档聊天</h1>
        <p>{{ workspace.projectName(project) }} · 归档保留消息与历史记录。</p>
      </div>
      <button
        :disabled="bucket.loading"
        @click="workspace.loadScopeThreads(project, true, true)"
      >
        刷新
      </button>
    </header>
    <p v-if="bucket.error || workspace.archiveError" class="error" role="alert">
      {{ bucket.error || workspace.archiveError }}
    </p>
    <p v-if="bucket.loading" role="status">正在加载…</p>
    <p v-else-if="bucket.loaded && !bucket.items.length && !bucket.error">
      当前空间没有已归档聊天。
    </p>
    <ul class="archive-list">
      <li v-for="thread in bucket.items" :key="thread.id">
        <RouterLink
          :to="{
            path: `/threads/${thread.id}`,
            query: project ? { project } : {},
          }"
          >{{ thread.title }}</RouterLink
        >
        <button
          :disabled="workspace.archiveBusy"
          @click="workspace.archiveThread(thread, false)"
        >
          恢复会话
        </button>
      </li>
    </ul>
    <button
      v-if="bucket.nextOffset !== null"
      :disabled="bucket.loading"
      @click="workspace.loadScopeThreads(project, false, true)"
    >
      加载更多
    </button>
    <RouterLink :to="{ path: '/threads', query: project ? { project } : {} }"
      >返回当前空间</RouterLink
    >
  </section>
</template>
<style scoped>
.archive-list {
  list-style: none;
  padding: 0;
}
.archive-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 0;
  border-bottom: 1px solid var(--border);
}
.archive-list a {
  overflow-wrap: anywhere;
}
</style>
