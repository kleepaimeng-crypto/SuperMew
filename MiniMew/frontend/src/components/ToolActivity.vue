<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type {
  ProcessStepData,
  ProcessDetailData,
  RunStatus,
} from "../generated/run-event-v1";
import type { ToolProjection } from "../events/runEventReducer";
import ProcessTask from "./ProcessTask.vue";
import { processTasks } from "../events/processTasks";
import { errorPresentation } from "../runErrors";
const props = defineProps<{
  steps?: readonly ProcessStepData[];
  stepDetails?: readonly ProcessDetailData[];
  runStatus?: RunStatus;
  tools: readonly ToolProjection[];
  loading: boolean;
  loaded: boolean;
  error: string;
}>();
const tasks = computed(() =>
  processTasks(props.steps ?? [], props.stepDetails ?? []),
);
const active = computed(() =>
  ["pending", "running", "waiting_input", "resume_pending"].includes(
    props.runStatus ?? "",
  ),
);
const expanded = ref(false);
watch(
  () => props.runStatus,
  (status) => {
    if (status === "succeeded") expanded.value = false;
    else if (status) expanded.value = true;
  },
  { immediate: true },
);
const emit = defineEmits<{ load: []; layout: [] }>();
const names: Record<string, string> = {
  knowledge_search: "查询知识库",
  read_skill_resource: "读取任务参考资料",
};
const standaloneTools = computed(() =>
  props.steps?.length
    ? props.tools.filter((t) => t.name !== "knowledge_search")
    : props.tools,
);
const labels: Record<ToolProjection["status"], string> = {
  running: "执行中",
  completed: "已完成",
  failed: "未完成",
  cancelled: "已取消",
  interrupted: "执行中断",
};
function toggled(event: Event) {
  expanded.value = (event.target as HTMLDetailsElement).open;
  if (expanded.value) emit("load");
  if (active.value || !expanded.value) emit("layout");
}
</script>

<template>
  <details class="tool-activity" :open="expanded" @toggle="toggled">
    <summary>
      {{
        active
          ? runStatus === "waiting_input"
            ? "等待补充"
            : "执行中"
          : "执行过程"
      }}
      <span v-if="steps?.length">
        · {{ tasks.length + standaloneTools.length }}项任务</span
      >
      <span v-else-if="tools.length"> · {{ tools.length }}次工具调用</span>
      <span v-if="runStatus === 'succeeded'"> · 已完成</span>
    </summary>
    <p v-if="loading" role="status">正在加载执行过程…</p>
    <ol v-if="steps?.length" class="process-tasks">
      <li v-for="task in tasks" :key="task.step.step_id">
        <ProcessTask :task="task" :run-status="runStatus" />
      </li>
    </ol>
    <ol v-if="standaloneTools.length">
      <li v-for="tool in standaloneTools" :key="tool.id">
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
    <p v-else-if="active && !steps?.length">
      请求已接收，执行步骤会依次显示在这里。
    </p>
    <p v-else-if="loaded && !steps?.length">这次回答没有已保存的工具记录。</p>
    <p v-else-if="!loading && !error && !steps?.length">
      工具执行后会在这里显示记录。
    </p>
    <div v-if="error" class="tool-error" role="status">
      <span>{{ error }}</span
      ><button type="button" class="quiet-button" @click="$emit('load')">
        重试加载过程
      </button>
    </div>
  </details>
</template>

<style scoped>
.process-tasks > li {
  display: block;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.step-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.process-tasks ul {
  padding-left: 16px;
}
.process-tasks ul li {
  display: block;
  border: 0;
  padding: 4px 0;
}
.tool-activity {
  margin: 8px 0 14px;
  padding: 8px 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.65;
  color: var(--ink-secondary);
}
summary {
  cursor: pointer;
  width: fit-content;
  padding: 2px 0;
  font-weight: 500;
  color: var(--ink-secondary);
}
summary:hover {
  color: var(--accent);
}
ol {
  list-style: none;
  padding: 0;
  margin: 6px 0 0;
}
li {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 16px;
  max-width: 460px;
  padding: 5px 0;
  border-bottom: 0;
}
.tool-failure-reason {
  flex-basis: 100%;
  overflow-wrap: anywhere;
  color: var(--warn);
}
.tool-state {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  color: var(--ink-muted);
}
.tool-failed,
.tool-error {
  color: var(--warn);
}
.tool-error {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
p {
  margin: 6px 0;
  color: inherit;
}
@media (pointer: coarse) {
  summary {
    min-height: 36px;
    display: list-item;
    padding: 8px 0;
  }
}
</style>
