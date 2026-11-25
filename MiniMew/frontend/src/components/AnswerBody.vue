<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ChatMessage } from "../chat";
import { answerBlocks, type AnswerView } from "../answers";
import { renderAnswerParts, renderMarkdown } from "../markdown";

const props = defineProps<{ message: ChatMessage; answer?: AnswerView }>();
const emit = defineEmits<{ retry: []; refreshSource: [] }>();
function toggleCitation(id: string) {
  selected.value = selected.value === id ? null : id;
  if (selected.value) emit("refreshSource");
}
function onAnswerClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null;
  const button = target?.closest<HTMLButtonElement>("button[data-citation-id]");
  const id = button?.dataset.citationId;
  if (id) toggleCitation(id);
}
const selected = ref<string | null>(null);
const materials = computed(() =>
  props.answer?.state === "loaded" &&
  props.answer.messageId === props.message.id &&
  props.answer.content === props.message.content
    ? props.answer.materials
    : null,
);
const renderedBlocks = computed(() =>
  materials.value
    ? answerBlocks(props.message.content, materials.value).map((block) => ({
        kind: block.kind,
        html: renderAnswerParts(block.parts, selected.value),
      }))
    : [],
);
const renderedPlain = computed(() =>
  !materials.value && props.message.content
    ? renderMarkdown(props.message.content)
    : "",
);
const source = computed(() =>
  materials.value?.evidence.find((e) => e.citation_id === selected.value),
);
watch(
  () => [props.message.id, props.message.content],
  () => (selected.value = null),
);
</script>

<template>
  <div class="answer-body">
    <template v-if="materials">
      <section
        v-for="block in renderedBlocks"
        :key="block.kind"
        class="answer-section"
      >
        <h4>{{ block.kind === "document" ? "文档依据" : "模型补充" }}</h4>
        <p v-if="block.kind === 'supplement'" class="answer-context">
          以下内容未由本次资料验证。
        </p>
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div
          class="answer-text markdown-body"
          @click="onAnswerClick"
          v-html="block.html"
        />
      </section>
      <section v-if="source" class="evidence-detail" aria-label="引用原文">
        <p
          v-if="answer?.withdrawnDocIds?.includes(source.document_id)"
          role="status"
        >
          此文档已下线，以下是本次回答保存的历史引用。
        </p>
        <div class="evidence-heading">
          <h4>{{ source.title_path.join(" / ") || "文档片段" }}</h4>
          <button type="button" class="quiet-button" @click="selected = null">
            收起原文
          </button>
        </div>
        <p
          class="evidence-location"
          :title="`文档 ${source.document_id} · 版本 ${source.version_id}`"
        >
          第{{ source.start_line }}–{{ source.end_line }}行 · 版本
          {{ source.version_id.slice(0, 8) }}
        </p>
        <blockquote>{{ source.text }}</blockquote>
      </section>
    </template>
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div
      v-else-if="message.content"
      class="message-body markdown-body"
      v-html="renderedPlain"
    />
    <div v-else class="message-body">
      {{
        message.status === "waiting_input"
          ? "需要你补充信息后继续。"
          : message.status === "pending"
            ? "正在等待回复…"
            : message.status === "failed"
              ? "本次执行失败，未生成正文。具体原因见下方提示。"
              : message.status === "cancelled"
                ? "本次已取消，未生成正文。"
                : "本次没有可展示的正文。"
      }}
    </div>
    <div
      v-if="answer?.state === 'loading'"
      class="answer-loading"
      role="status"
    >
      正在加载引用资料…
    </div>
    <div v-if="answer?.state === 'error'" class="answer-error" role="status">
      <span>{{ answer.error }}</span>
      <button type="button" class="quiet-button" @click="$emit('retry')">
        重试加载引用
      </button>
    </div>
  </div>
</template>

<style scoped>
.answer-section + .answer-section {
  margin-top: 18px;
}
h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-text);
}
.answer-text {
  margin-top: 6px;
  line-height: 1.75;
  font-size: 15px;
  color: var(--ink);
}
.markdown-body {
  white-space: normal;
  overflow-wrap: anywhere;
}
.markdown-body :deep(> *:first-child) {
  margin-top: 0;
}
.markdown-body :deep(> *:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(p) {
  margin: 0 0 10px;
  color: var(--ink);
  line-height: 1.75;
}
.markdown-body :deep(strong) {
  font-weight: 650;
  color: var(--ink);
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  margin: 16px 0 8px;
  font-weight: 650;
  line-height: 1.4;
  color: var(--ink);
}
.markdown-body :deep(h1) {
  font-size: 19px;
}
.markdown-body :deep(h2) {
  font-size: 17px;
}
.markdown-body :deep(h3) {
  font-size: 15.5px;
}
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  font-size: 15px;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 6px 0 10px;
  padding-left: 22px;
}
.markdown-body :deep(li) {
  margin: 4px 0;
  line-height: 1.75;
}
.markdown-body :deep(li > p) {
  margin: 0 0 4px;
}
.markdown-body :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.markdown-body :deep(a:hover) {
  color: var(--accent-hover);
}
.markdown-body :deep(blockquote) {
  margin: 10px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--accent-border);
  background: var(--bg-subtle);
  color: var(--ink-secondary);
  border-radius: 0 6px 6px 0;
}
.markdown-body :deep(hr) {
  border: 0;
  border-top: 1px solid var(--border);
  margin: 14px 0;
}
.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13.5px;
  display: block;
  overflow-x: auto;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: var(--bg-subtle);
  font-weight: 600;
}
.markdown-body :deep(code) {
  font-family:
    ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
    monospace;
  font-size: 0.88em;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-muted);
  color: var(--ink);
}
.markdown-body :deep(pre) {
  margin: 10px 0;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  overflow-x: auto;
  line-height: 1.6;
}
.markdown-body :deep(pre code) {
  display: block;
  padding: 0;
  background: transparent;
  border-radius: 0;
  font-size: 13px;
  white-space: pre;
}
.markdown-body :deep(.hljs-comment),
.markdown-body :deep(.hljs-quote) {
  color: #64748b;
  font-style: italic;
}
.markdown-body :deep(.hljs-keyword),
.markdown-body :deep(.hljs-selector-tag),
.markdown-body :deep(.hljs-subst) {
  color: #7c3aed;
  font-weight: 600;
}
.markdown-body :deep(.hljs-number),
.markdown-body :deep(.hljs-literal),
.markdown-body :deep(.hljs-variable),
.markdown-body :deep(.hljs-template-variable) {
  color: #0369a1;
}
.markdown-body :deep(.hljs-string),
.markdown-body :deep(.hljs-doctag) {
  color: #15803d;
}
.markdown-body :deep(.hljs-title),
.markdown-body :deep(.hljs-section),
.markdown-body :deep(.hljs-selector-id) {
  color: #1d4ed8;
  font-weight: 600;
}
.markdown-body :deep(.hljs-type),
.markdown-body :deep(.hljs-class .hljs-title),
.markdown-body :deep(.hljs-built_in) {
  color: #b45309;
}
.markdown-body :deep(.hljs-attr),
.markdown-body :deep(.hljs-attribute),
.markdown-body :deep(.hljs-name),
.markdown-body :deep(.hljs-tag) {
  color: #be123c;
}
.markdown-body :deep(.hljs-addition) {
  color: #15803d;
  background: #f0fdf4;
}
.markdown-body :deep(.hljs-deletion) {
  color: #b91c1c;
  background: #fef2f2;
}
.answer-context,
.evidence-location {
  color: var(--ink-muted);
  font-size: 12px;
  margin: 4px 0 0;
  line-height: 1.6;
}
.markdown-body :deep(.citation-button) {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--accent-border);
  background: var(--bg-accent-softer);
  color: var(--accent-text);
  padding: 0 6px;
  margin: 0 2px;
  border-radius: 5px;
  height: 22px;
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
  vertical-align: baseline;
}
.markdown-body :deep(.citation-button:hover),
.markdown-body :deep(.citation-button[aria-expanded="true"]) {
  background: var(--bg-accent-soft);
  border-color: var(--accent);
}
.evidence-detail {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.evidence-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.evidence-detail blockquote {
  margin: 10px 0 0;
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink-secondary);
  overflow-wrap: anywhere;
}
.answer-loading,
.answer-error {
  color: var(--ink-muted);
  font-size: 12px;
  margin-top: 8px;
}
.answer-error {
  color: var(--warn);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
@media (pointer: coarse) {
  .markdown-body :deep(.citation-button) {
    height: 30px;
    padding: 0 8px;
  }
}
</style>
