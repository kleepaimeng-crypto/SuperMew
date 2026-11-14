<script setup lang="ts">
import type { ToolProjection } from "../events/runEventReducer";
import { errorPresentation } from "../runErrors";
defineProps<{
  tools: readonly ToolProjection[];
  loading: boolean;
  loaded: boolean;
  error: string;
}>();
const emit = defineEmits<{ load: [] }>();
const names: Record<string, string> = { knowledge_search: "查询知识库" };
const labels: Record<ToolProjection["status"], string> = {
  running: "执行中",
  completed: "已完成",
  failed: "未完成",
  cancelled: "已取消",
  interrupted: "执行中断",
};
function toggled(event: Event) {
  if ((event.target as HTMLDetailsElement).open) emit("load");
}
</script>

<template>
  <details class="tool-activity" @toggle="toggled">
    <summary>
      执行过程<span v-if="tools.length"> · {{ tools.length }}次工具调用</span>
    </summary>
    <p v-if="loading" role="status">正在加载执行过程…</p>
    <ol v-if="tools.length">
      <li v-for="tool in tools" :key="tool.id">
        <span>{{ names[tool.name] || tool.name }}</span>
        <span
          class="tool-state"
          :class="{
            'tool-failed':
              tool.status === 'failed' || tool.status === 'interrupted',
          }"
          >{{ labels[tool.status] }}</span
        >
        <p v-if="tool.status === 'failed'" class="tool-failure-reason">
          {{ errorPresentation(tool.errorCode).reason }}
          <code>{{ errorPresentation(tool.errorCode).code }}</code>
        </p>
      </li>
    </ol>
    <p v-else-if="loaded">这次回答没有已保存的工具记录。</p>
    <p v-else-if="!loading && !error">工具执行后会在这里显示记录。</p>
    <div v-if="error" class="tool-error" role="status">
      <span>{{ error }}</span
      ><button type="button" class="quiet-button" @click="$emit('load')">
        重试加载过程
      </button>
    </div>
  </details>
</template>

<style scoped>
.tool-activity {
  margin-top: 12px;
  font-size: 12px;
  color: #536552;
}
summary {
  cursor: pointer;
  width: fit-content;
  padding: 5px 0;
}
summary:hover {
  color: #294e39;
  text-decoration: underline;
  text-underline-offset: 3px;
}
ol {
  list-style: none;
  padding: 0;
  margin: 8px 0;
}
li {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 20px;
  max-width: 420px;
  padding: 7px 0;
  border-bottom: 1px solid #dde2dc;
}
.tool-failure-reason {
  flex-basis: 100%;
  overflow-wrap: anywhere;
  color: #76542f;
}
.tool-state {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.tool-failed,
.tool-error {
  color: #76542f;
}
.tool-error {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
p {
  margin: 8px 0;
  color: inherit;
}
@media (pointer: coarse) {
  summary {
    min-height: 44px;
    display: list-item;
    padding: 12px 0;
  }
}
</style>
