<script setup lang="ts">
import { computed } from "vue";
import type { RunStatus, ProcessSourceDetail } from "../generated/run-event-v1";
import {
  type ProcessTask,
  taskTitle,
  taskState,
  stageNames,
  outcomes,
  materialCount,
  elapsed,
} from "../events/processTasks";
import { errorPresentation } from "../runErrors";
const props = defineProps<{ task: ProcessTask; runStatus?: RunStatus }>();
const step = computed(() => props.task.step);
const gaps = computed(() => props.task.detail?.missing ?? []);
const sources = computed(() => props.task.detail?.sources ?? []);
const planned = computed(
  () =>
    props.task.detail?.questions.filter(
      (q) => !props.task.children.some((c) => c.step.query === q),
    ) ?? [],
);
const records = computed(() => [props.task.step, ...props.task.records]);
const sourceDetails = computed(() => {
  const documents = new Map<string, ProcessSourceDetail>();
  for (const record of records.value) {
    const detail = props.task.details.find((d) => d.step_id === record.step_id);
    for (const source of detail?.sources ?? []) {
      const existing = documents.get(source.version_id);
      documents.set(source.version_id, {
        ...source,
        sections: [
          ...new Set([...(existing?.sections ?? []), ...source.sections]),
        ],
      });
    }
  }
  return [...documents.values()];
});
const legacySources = computed(() =>
  sourceDetails.value.length
    ? []
    : [
        ...new Map(
          records.value
            .flatMap((record) => record.sources)
            .map((s) => [s.version_id, s]),
        ).values(),
      ],
);
const warnings = computed(() =>
  [...new Set(records.value.map((s) => s.outcome))].filter(
    (code) =>
      code.startsWith("RERANK_") || code === "HYBRID_LAYOUT_UNAVAILABLE",
  ),
);
function result(code: string) {
  if (
    code === "RERANK_PROVIDER_AUTH_FAILED" ||
    code === "RERANK_CREDENTIAL_UNAVAILABLE"
  )
    return "重排凭据不可用，保留召回顺序";
  if (code === "RERANK_PROVIDER_TIMEOUT")
    return "重排服务响应超时，保留召回顺序";
  if (code === "RERANK_PROVIDER_UNAVAILABLE")
    return "重排服务暂不可用，保留召回顺序";
  if (code.startsWith("RERANK_")) return "重排未完成，保留召回顺序";
  if (code === "HYBRID_LAYOUT_UNAVAILABLE")
    return "当前索引不支持混合检索，采用向量召回";
  return outcomes[code] ?? code;
}
</script>
<template>
  <div class="task-row" :class="{ subtask: step.kind === 'subquestion' }">
    <div class="task-heading">
      <strong>{{ taskTitle(task) }}</strong
      ><span>{{ taskState(task, runStatus) }}</span>
    </div>
    <p v-if="step.kind === 'retrieval' && step.status === 'completed'">
      {{ materialCount(step) }}
    </p>
    <p v-if="step.kind === 'assessment' || step.kind === 'subquestion'">
      {{
        outcomes[step.outcome] ||
        (step.status === "running"
          ? "正在核对资料覆盖情况"
          : step.status === "failed"
            ? "本阶段未完成"
            : "处理结束")
      }}
    </p>
    <p v-if="step.status === 'failed'" class="task-error">
      {{
        step.outcome.startsWith("PROVIDER_")
          ? errorPresentation(step.outcome, step.kind).reason
          : "本阶段未完成"
      }}
      <code>{{ step.outcome }}</code>
    </p>
    <p v-for="warning in warnings" :key="warning" class="task-error">
      {{ result(warning) }}
    </p>
    <p v-if="step.kind === 'subquestion'" class="subquery">{{ step.query }}</p>
    <ul v-if="gaps.length" class="gaps">
      <li v-for="gap in gaps.slice(0, 2)" :key="gap">尚缺：{{ gap }}</li>
    </ul>
    <details v-if="gaps.length > 2">
      <summary>查看全部{{ gaps.length }}项缺口</summary>
      <ul>
        <li v-for="gap in gaps" :key="gap">{{ gap }}</li>
      </ul>
    </details>
    <ul v-if="step.kind === 'retrieval' && sources.length" class="source-names">
      <li v-for="source in sources" :key="source.version_id">
        {{ source.name }}
      </li>
    </ul>
    <p
      v-if="
        step.kind === 'assessment' &&
        step.outcome === 'insufficient' &&
        !task.detail
      "
      class="muted"
    >
      旧记录未保存具体缺口。
    </p>
    <div v-if="task.children.length || planned.length" class="subtasks">
      <ProcessTask
        v-for="child in task.children"
        :key="child.step.step_id"
        :task="child"
        :run-status="runStatus"
      />
      <p v-for="question in planned" :key="question" class="planned">
        尚未开始：{{ question }}
      </p>
    </div>
    <details class="retrieval-details">
      <summary>
        {{
          step.kind === "retrieval" || step.kind === "subquestion"
            ? "检索详情"
            : "过程详情"
        }}
      </summary>
      <p v-if="step.query && step.kind !== 'subquestion'">
        完整查询：{{ step.query }}
      </p>
      <ol class="technical-records">
        <li v-for="record in records" :key="record.step_id">
          <span>{{ stageNames[record.kind] }}</span> ·
          {{
            record.status === "running"
              ? runStatus === "failed" || runStatus === "cancelled"
                ? "已中断"
                : "执行中"
              : record.status === "failed"
                ? "未完成"
                : "已完成"
          }}<template v-if="materialCount(record)">
            · {{ materialCount(record) }}</template
          ><template v-if="record.status !== 'running'">
            · {{ elapsed(record.elapsed_ms) }}</template
          >
          <p v-if="record.kind === 'rerank' && record.summary">
            {{ record.summary }}
          </p>
          <p
            v-if="
              record.outcome &&
              !['ok', 'text', 'tool_calls'].includes(record.outcome)
            "
          >
            结果：{{ result(record.outcome) }}
          </p>
        </li>
      </ol>
      <ul v-if="legacySources.length">
        <li v-for="source in legacySources" :key="source.version_id">
          来源摘要（旧记录）：{{ source.title }} · 版本
          {{ source.version_id.slice(0, 8) }}
        </li>
      </ul>
      <div
        v-for="source in sourceDetails"
        :key="source.version_id"
        class="source-detail"
      >
        <strong>{{ source.name }}</strong>
        <p>
          版本 {{ source.version_number || "未记录" }} ·
          {{ source.version_id }}
        </p>
        <ul>
          <li v-for="section in source.sections" :key="section">
            {{ section }}
          </li>
        </ul>
      </div>
      <p class="muted">父任务耗时包含内部阶段，并行耗时不相加。</p>
    </details>
  </div>
</template>
<style scoped>
.task-row {
  padding: 14px 0;
  border-bottom: 1px solid #e5e9e3;
  overflow-wrap: anywhere;
  min-width: 0;
}
.task-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.task-heading span,
.muted {
  font-size: 12px;
  color: #62705f;
}
p {
  margin: 7px 0;
  color: inherit;
  line-height: 1.7;
}
ul {
  margin: 7px 0;
  padding-left: 18px;
}
.subtasks {
  margin: 10px 0 0 12px;
  padding-left: 12px;
  border-left: 1px solid #dde2dc;
}
.subquery,
.planned {
  font-size: 13px;
}
.retrieval-details {
  font-size: 13px;
  margin-top: 8px;
}
summary {
  cursor: pointer;
  padding: 6px 0;
  text-decoration: underline;
  text-underline-offset: 3px;
  width: fit-content;
}
.technical-records {
  padding-left: 20px;
}
.technical-records > li {
  padding: 8px 0;
}
.source-detail {
  padding: 10px 0;
}
.task-error {
  color: #76542f;
}
@media (pointer: coarse) {
  summary {
    min-height: 44px;
  }
}
</style>
