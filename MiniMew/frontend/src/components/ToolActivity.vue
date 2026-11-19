<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ProcessStepData, RunStatus } from "../generated/run-event-v1";
import type { ToolProjection } from "../events/runEventReducer";
import { errorPresentation } from "../runErrors";
const props = defineProps<{
  steps?: readonly ProcessStepData[];
  runStatus?: RunStatus;
  tools: readonly ToolProjection[];
  loading: boolean;
  loaded: boolean;
  error: string;
}>();
const kinds: Record<string, string> = {
  model: "模型请求",
  retrieval: "检索资料",
  dense: "向量召回",
  bm25: "关键词召回",
  fusion: "融合排序",
  merge: "同节补全",
  rerank: "重排候选",
  assessment: "证据评判",
  rewrite: "改写查询",
  plan: "拆解问题",
  subquestion: "子问题",
};
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
function description(step: ProcessStepData) {
  if (step.status === "failed") return "本阶段未完成";
  if (step.status === "completed")
    return step.summary || `${kinds[step.kind]}结束`;
  const actions: Record<string, string> = {
    model: "正在请求模型",
    retrieval: "正在检索相关资料",
    dense: "正在匹配语义相关片段",
    bm25: "正在匹配关键词",
    fusion: "正在合并检索结果",
    merge: "正在补全同节材料",
    rerank: "正在重排候选片段",
    assessment: "正在评判材料是否足够",
    rewrite: "正在改写检索问题",
    plan: "正在拆解检索问题",
    subquestion: "正在处理子问题",
  };
  if (props.runStatus === "waiting_input") return "已暂停，等待补充信息";
  if (["failed", "cancelled"].includes(props.runStatus ?? ""))
    return "本阶段已停止";
  return actions[step.kind] ?? kinds[step.kind]!;
}
function state(step: ProcessStepData) {
  if (step.status === "failed") return "未完成";
  if (step.status === "completed")
    return step.outcome &&
      ![
        "ok",
        "text",
        "tool_calls",
        "sufficient",
        "insufficient",
        "ambiguous",
        "no_documents",
      ].includes(step.outcome)
      ? "已降级"
      : "已完成";
  return props.runStatus === "waiting_input"
    ? "等待补充"
    : props.runStatus === "cancelled"
      ? "已取消"
      : ["succeeded", "failed"].includes(props.runStatus ?? "")
        ? "执行中断"
        : "执行中";
}
function depth(step: ProcessStepData) {
  let n = 0,
    id = step.parent_step_id;
  while (id && n < 4) {
    const parent = props.steps?.find((p) => p.step_id === id);
    if (!parent) break;
    n++;
    id = parent.parent_step_id;
  }
  return n;
}
const outcomes: Record<string, string> = {
  sufficient: "材料充分",
  insufficient: "材料不足",
  ambiguous: "需要澄清",
  no_documents: "无已发布文档",
};
function outcomeText(step: ProcessStepData) {
  if (step.status === "failed")
    return step.outcome.startsWith("PROVIDER_")
      ? errorPresentation(step.outcome, step.kind).reason
      : "本阶段未完成";
  return (
    outcomes[step.outcome] ||
    (step.kind === "rerank" ? "重排未完成，保留召回顺序" : "阶段采用降级结果")
  );
}
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
      <span v-if="steps?.length"> · {{ steps.length }}个步骤</span>
      <span v-else-if="tools.length"> · {{ tools.length }}次工具调用</span>
      <span v-if="runStatus === 'succeeded'"> · 已完成</span>
    </summary>
    <p v-if="loading" role="status">正在加载执行过程…</p>
    <ol v-if="steps?.length" class="process-steps">
      <li
        v-for="step in steps"
        :key="step.step_id"
        :style="{ marginLeft: depth(step) * 12 + 'px' }"
      >
        <div class="step-heading">
          <strong>{{ kinds[step.kind] }}</strong
          ><span
            >{{ state(step)
            }}<template v-if="step.status !== 'running'">
              · {{ (step.elapsed_ms / 1000).toFixed(1) }}秒</template
            ></span
          >
        </div>
        <p class="step-description">
          {{ description(step)
          }}<span v-if="step.count"> · {{ step.count }}项</span>
        </p>
        <p v-if="step.query">查询：{{ step.query }}</p>
        <p
          v-if="
            (step.status !== 'failed' ||
              step.outcome.startsWith('PROVIDER_')) &&
            step.outcome &&
            !['ok', 'text', 'tool_calls'].includes(step.outcome)
          "
        >
          结果：{{ outcomeText(step) }}
        </p>
        <ul v-if="step.sources.length">
          <li v-for="source in step.sources" :key="source.version_id">
            {{ source.title }}
            <small>版本 {{ source.version_id.slice(0, 8) }}</small>
          </li>
        </ul>
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
.process-steps > li {
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
.process-steps ul {
  padding-left: 16px;
}
.process-steps ul li {
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
  border-bottom: 1px solid #e5e9e3;
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
