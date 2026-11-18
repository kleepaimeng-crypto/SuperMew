<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ChatMessage } from "../chat";
import { answerBlocks, type AnswerView } from "../answers";

const props = defineProps<{ message: ChatMessage; answer?: AnswerView }>();
const emit = defineEmits<{ retry: []; refreshSource: [] }>();
function toggleCitation(id: string) {
  selected.value = selected.value === id ? null : id;
  if (selected.value) emit("refreshSource");
}
const selected = ref<string | null>(null);
const materials = computed(() =>
  props.answer?.state === "loaded" &&
  props.answer.messageId === props.message.id &&
  props.answer.content === props.message.content
    ? props.answer.materials
    : null,
);
const blocks = computed(() =>
  materials.value ? answerBlocks(props.message.content, materials.value) : [],
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
      <section v-for="block in blocks" :key="block.kind" class="answer-section">
        <h4>{{ block.kind === "document" ? "文档依据" : "模型补充" }}</h4>
        <p v-if="block.kind === 'supplement'" class="answer-context">
          以下内容未由本次资料验证。
        </p>
        <div class="answer-text">
          <template v-for="(part, index) in block.parts" :key="index"
            ><button
              v-if="part.citationId"
              type="button"
              class="citation-button"
              :aria-label="`查看引用 ${part.citationId}`"
              :aria-expanded="selected === part.citationId"
              @click="toggleCitation(part.citationId!)"
            >
              {{ part.text }}</button
            ><span v-else>{{ part.text }}</span></template
          >
        </div>
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
    <div v-else class="message-body">
      {{
        message.content ||
        (message.status === "waiting_input"
          ? "需要你补充信息后继续。"
          : message.status === "pending"
            ? "正在等待回复…"
            : message.status === "failed"
              ? "本次执行失败，未生成正文。具体原因见下方提示。"
              : message.status === "cancelled"
                ? "本次已取消，未生成正文。"
                : "本次没有可展示的正文。")
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
  margin-top: 22px;
}
h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 650;
  color: #435e4a;
}
.answer-text {
  margin-top: 8px;
  white-space: pre-wrap;
  line-height: 1.8;
  font-size: 15px;
}
.answer-context,
.evidence-location {
  color: #536552;
  font-size: 12px;
  margin: 4px 0 0;
  line-height: 1.6;
}
.citation-button {
  display: inline-block;
  border: 0;
  background: #e8eee4;
  color: #294e39;
  padding: 2px 5px;
  margin: 0 2px;
  border-radius: 4px;
  min-height: 28px;
  cursor: pointer;
  font-size: 12px;
  vertical-align: baseline;
}
.citation-button:hover,
.citation-button[aria-expanded="true"] {
  background: #d4e2ce;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.evidence-detail {
  margin-top: 18px;
  padding: 14px 0;
  border-top: 1px solid #cdd8c8;
  border-bottom: 1px solid #cdd8c8;
}
.evidence-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
blockquote {
  margin: 12px 0 0;
  white-space: pre-wrap;
  font-size: 14px;
  line-height: 1.8;
  color: #202c29;
  overflow-wrap: anywhere;
}
.answer-loading,
.answer-error {
  color: #536552;
  font-size: 12px;
  margin-top: 10px;
}
.answer-error {
  color: #76542f;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
@media (pointer: coarse) {
  .citation-button {
    min-height: 44px;
    padding: 4px 8px;
  }
}
</style>
