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
const names: Record<string, string> = { knowledge_search: "查询知识库" };
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
      <span v-if="steps?.length"> · {{ tasks.length }}项任务</span>
      <span v-else-if="tools.length"> · {{ tools.length }}次工具调用</span>
      <span v-if="runStatus === 'succeeded'"> · 已完成</span>
    </summary>
    <p v-if="loading" role="status">正在加载执行过程…</p>
    <ol v-if="steps?.length" class="process-tasks">
      <li v-for="task in tasks" :key="task.step.step_id">
        <ProcessTask :task="task" :run-status="runStatus" />
      </li>
    </ol>
    <ol v-if="tools.length && !steps?.length">
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
  margin-top: 12px;
  font-size: 14px;
  line-height: 1.7;
  color: #536552;
  margin-bottom: 18px;
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
  border-bottom: 0;
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
