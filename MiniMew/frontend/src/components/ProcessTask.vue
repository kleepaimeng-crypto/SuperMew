<script setup lang="ts">
import { computed } from "vue";
import type { RunStatus, ProcessSourceDetail } from "../generated/run-event-v1";
import {
  type ProcessTask,
  taskTitle,
  taskState,
  taskElapsed,
  decisionSummary,
  stageNames,
  outcomes,
  materialCount,
  elapsed,
} from "../events/processTasks";
import { errorPresentation } from "../runErrors";

const props = defineProps<{ task: ProcessTask; runStatus?: RunStatus }>();
const step = computed(() => props.task.step);
const stateLabel = computed(() => taskState(props.task, props.runStatus));
const stepElapsed = computed(() => taskElapsed(props.task));
const summaryText = computed(() => decisionSummary(props.task));
const gaps = computed(() => props.task.detail?.missing ?? []);
const sources = computed(() => props.task.detail?.sources ?? []);
const planned = computed(
  () =>
    props.task.detail?.questions.filter(
      (q) => !props.task.children.some((c) => c.step.query === q),
    ) ?? [],
);
const allRecords = computed(() => [props.task.step, ...props.task.records]);
const internalRecords = computed(() => props.task.records);
const sourceDetails = computed(() => {
  const documents = new Map<string, ProcessSourceDetail>();
  for (const record of allRecords.value) {
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
          allRecords.value
            .flatMap((record) => record.sources)
            .map((s) => [s.version_id, s]),
        ).values(),
      ],
);
const warnings = computed(() =>
  [...new Set(allRecords.value.map((s) => s.outcome))].filter(
    (code) =>
      code.startsWith("RERANK_") || code === "HYBRID_LAYOUT_UNAVAILABLE",
  ),
);
const hasDetails = computed(
  () =>
    internalRecords.value.length > 0 ||
    sourceDetails.value.length > 0 ||
    legacySources.value.length > 0,
);
const nodeTone = computed(() => {
  const s = stateLabel.value;
  if (s === "未完成" || s === "部分未完成" || s === "执行中断") return "failed";
  if (s === "进行中" || s === "等待补充" || s === "正在安排补查")
    return "running";
  return "completed";
});

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
  <div
    class="task-row"
    :class="[{ subtask: step.kind === 'subquestion' }, `is-${nodeTone}`]"
  >
    <span class="task-node" aria-hidden="true" />
    <div class="task-body">
      <div class="task-heading">
        <strong>{{ taskTitle(task) }}</strong>
        <span class="task-meta-state">
          {{ stateLabel
          }}<template v-if="stepElapsed"> · {{ stepElapsed }}</template>
        </span>
      </div>

      <p v-if="summaryText" class="task-decision">{{ summaryText }}</p>

      <div v-if="step.query" class="query-callout">
        <span class="query-label">查询</span>
        <span class="query-text">{{ step.query }}</span>
      </div>

      <div
        v-if="
          step.kind === 'retrieval' &&
          (step.status === 'completed' || sources.length)
        "
        class="retrieval-summary-row"
      >
        <span v-if="step.status === 'completed'" class="hit-badge">
          {{ materialCount(step) }}
        </span>
        <span
          v-for="source in sources"
          :key="source.version_id"
          class="source-chip"
        >
          {{ source.name }}
        </span>
      </div>

      <p
        v-if="step.kind === 'assessment' || step.kind === 'subquestion'"
        class="assessment-line"
        :class="{
          'is-sufficient': step.outcome === 'sufficient',
          'is-insufficient':
            step.outcome === 'insufficient' ||
            step.outcome === 'ambiguous' ||
            step.outcome === 'conflicting',
        }"
      >
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

      <ul v-if="gaps.length" class="gaps">
        <li v-for="gap in gaps.slice(0, 2)" :key="gap">尚缺：{{ gap }}</li>
      </ul>
      <details v-if="gaps.length > 2" class="extra-gaps">
        <summary>查看全部 {{ gaps.length }} 项缺口</summary>
        <ul>
          <li v-for="gap in gaps" :key="gap">{{ gap }}</li>
        </ul>
      </details>

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

      <details v-if="hasDetails" class="retrieval-details">
        <summary>
          {{
            step.kind === "retrieval" || step.kind === "subquestion"
              ? "检索详情"
              : "过程详情"
          }}
        </summary>
        <div class="details-panel">
          <ol v-if="internalRecords.length" class="pipeline-list">
            <li
              v-for="record in internalRecords"
              :key="record.step_id"
              class="pipeline-item"
            >
              <div class="pipeline-main">
                <span class="pipeline-stage">{{
                  stageNames[record.kind]
                }}</span>
                <span class="pipeline-sep">·</span>
                <span>{{
                  record.status === "running"
                    ? runStatus === "failed" || runStatus === "cancelled"
                      ? "已中断"
                      : "执行中"
                    : record.status === "failed"
                      ? "未完成"
                      : "已完成"
                }}</span>
                <template v-if="materialCount(record)">
                  <span class="pipeline-sep">·</span>
                  <span>{{ materialCount(record) }}</span>
                </template>
                <template v-if="record.status !== 'running'">
                  <span class="pipeline-sep">·</span>
                  <span class="pipeline-time">{{
                    elapsed(record.elapsed_ms)
                  }}</span>
                </template>
              </div>
              <p
                v-if="record.kind === 'rerank' && record.summary"
                class="pipeline-sub"
              >
                {{ record.summary }}
              </p>
              <p
                v-if="
                  record.outcome &&
                  !['ok', 'text', 'tool_calls'].includes(record.outcome)
                "
                class="pipeline-sub"
              >
                结果：{{ result(record.outcome) }}
              </p>
            </li>
          </ol>

          <ul v-if="legacySources.length" class="legacy-sources">
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
            <div class="source-detail-head">
              <strong>{{ source.name }}</strong>
              <span class="muted">
                版本 {{ source.version_number || "未记录" }} ·
                {{ source.version_id }}
              </span>
            </div>
            <ul v-if="source.sections.length" class="source-sections">
              <li v-for="section in source.sections" :key="section">
                {{ section }}
              </li>
            </ul>
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.task-row {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 10px;
  padding: 10px 0;
  overflow-wrap: anywhere;
  min-width: 0;
}

.task-row::before {
  content: "";
  position: absolute;
  left: 7px;
  top: 26px;
  bottom: -10px;
  width: 1.5px;
  background: var(--border);
}

:deep(.process-tasks > li:last-child) > .task-row::before,
.subtasks > .task-row:last-child::before {
  display: none;
}

.task-node {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin: 6px auto 0;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--bg-accent-soft);
  z-index: 1;
}

.task-row.is-running .task-node {
  background: var(--accent);
  animation: pulse-node 1.4s ease-in-out infinite;
}

.task-row.is-failed .task-node {
  background: var(--warn);
  box-shadow: 0 0 0 3px var(--warn-bg);
}

@keyframes pulse-node {
  0%,
  100% {
    box-shadow: 0 0 0 2px var(--bg-accent-soft);
  }
  50% {
    box-shadow: 0 0 0 5px var(--accent-ring);
  }
}

.task-body {
  min-width: 0;
}

.task-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.task-heading strong {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
}

.task-meta-state {
  font-size: 12px;
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.task-decision {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--ink-secondary);
  line-height: 1.55;
}

.query-callout {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--bg-canvas);
  border: 1px solid var(--border);
  font-size: 13px;
  line-height: 1.5;
}

.query-label {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-text);
  background: var(--bg-accent-soft);
  padding: 1px 6px;
  border-radius: 4px;
}

.query-text {
  color: var(--ink);
  min-width: 0;
}

.retrieval-summary-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.hit-badge {
  font-size: 12px;
  font-weight: 550;
  color: var(--ink-secondary);
}

.source-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--bg-canvas);
  border: 1px solid var(--border);
  color: var(--ink-secondary);
  font-size: 12px;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.assessment-line {
  margin: 5px 0 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-secondary);
}

.assessment-line.is-sufficient {
  color: var(--success);
}

.assessment-line.is-insufficient {
  color: var(--warn);
}

.muted {
  font-size: 12px;
  color: var(--ink-muted);
}

p {
  margin: 4px 0;
  color: inherit;
  line-height: 1.6;
}

.gaps {
  margin: 5px 0 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--ink-secondary);
}

.gaps li {
  margin: 2px 0;
}

.extra-gaps {
  margin-top: 4px;
  font-size: 12px;
}

.extra-gaps ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.subtasks {
  margin: 8px 0 0 2px;
  padding: 6px 0 2px 12px;
  border-left: 2px solid var(--accent-border);
}

.subtask {
  padding: 7px 0;
}

.planned {
  font-size: 12.5px;
  color: var(--ink-muted);
  margin: 4px 0;
}

.retrieval-details {
  font-size: 12px;
  margin-top: 7px;
}

summary {
  cursor: pointer;
  width: fit-content;
  color: var(--ink-muted);
  font-weight: 500;
  transition: color 0.12s;
}

summary:hover {
  color: var(--accent);
}

.details-panel {
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 9px;
  background: var(--bg-canvas);
  border: 1px solid var(--border);
}

.pipeline-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.pipeline-item {
  font-size: 12px;
  color: var(--ink-secondary);
}

.pipeline-main {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 5px;
}

.pipeline-stage {
  font-weight: 600;
  color: var(--ink);
}

.pipeline-sep {
  color: var(--border-strong);
}

.pipeline-time {
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}

.pipeline-sub {
  margin: 2px 0 0 10px;
  font-size: 12px;
  color: var(--ink-muted);
}

.legacy-sources {
  margin: 8px 0 0;
  padding-left: 16px;
}

.source-detail {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.source-detail:first-child {
  margin-top: 0;
  padding-top: 0;
  border-top: 0;
}

.source-detail-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.source-detail-head strong {
  font-size: 12.5px;
  color: var(--ink);
}

.source-sections {
  margin: 5px 0 0;
  padding-left: 16px;
  color: var(--ink-secondary);
}

.task-error {
  color: var(--warn);
  font-size: 13px;
}

.task-error code {
  font-size: 11px;
}
</style>
