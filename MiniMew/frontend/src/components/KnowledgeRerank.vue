<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError } from "../api";
import type { RerankSettings } from "../documents";
import { useDocumentsStore } from "../stores/documents";

const props = defineProps<{ project: string | null }>();
const documents = useDocumentsStore();
const settings = ref<RerankSettings>();
const source = ref<RerankSettings["source"]>("local");
const key = ref("");
const busy = ref(false);
const error = ref("");
const saved = ref(false);
let mounted = true;

const requiredKey = computed(
  () => source.value === "bailian" && !settings.value?.bailian_key_configured,
);
const canSave = computed(
  () =>
    settings.value &&
    !busy.value &&
    (!requiredKey.value || key.value.trim()) &&
    (source.value !== settings.value.source ||
      (source.value === "bailian" && key.value.trim())),
);

async function read() {
  busy.value = true;
  saved.value = false;
  error.value = "";
  try {
    const value = await documents.rerankSettings(props.project);
    if (!mounted) return;
    settings.value = value;
    source.value = value.source;
  } catch {
    if (mounted) error.value = "暂时无法读取重排设置。";
  } finally {
    if (mounted) busy.value = false;
  }
}

async function save() {
  if (!canSave.value) return;
  busy.value = true;
  saved.value = false;
  error.value = "";
  const apiKey = key.value.trim();
  key.value = "";
  try {
    const value = await documents.rerankSettings(props.project, {
      source: source.value,
      ...(apiKey ? { api_key: apiKey } : {}),
    });
    if (!mounted) return;
    settings.value = value;
    source.value = value.source;
    saved.value = true;
  } catch (cause) {
    const code =
      cause instanceof ApiError ? await cause.errorCode() : undefined;
    if (!mounted) return;
    error.value =
      code === "RERANK_KEY_REQUIRED"
        ? "请填写此知识库的百炼重排 API Key。"
        : code === "RERANK_KEY_STORAGE_UNAVAILABLE"
          ? "暂时无法加密保存 API Key，请检查服务端加密配置。"
          : "保存结果待确认，请刷新设置后重试。";
  } finally {
    if (mounted) busy.value = false;
  }
}

onMounted(() => void read());
onBeforeUnmount(() => {
  mounted = false;
  key.value = "";
});
</script>

<template>
  <section class="rerank-settings" aria-labelledby="rerank-heading">
    <h2 id="rerank-heading">知识库重排</h2>
    <p v-if="!settings && busy" role="status">正在读取重排设置…</p>
    <template v-if="settings">
      <p>
        重排模型 <strong>{{ settings.model }}</strong>
      </p>
      <p v-if="saved" role="status">已保存，从新对话请求开始生效。</p>
      <p v-if="project">
        项目模型已锁定。<RouterLink :to="`/projects/${project}`"
          >查看模型配置或更新 Key</RouterLink
        >
      </p>
      <details v-if="!project">
        <summary>更换重排模型</summary>
        <form @submit.prevent="save">
          <label for="rerank-source">重排来源</label>
          <select
            id="rerank-source"
            v-model="source"
            :disabled="busy"
            @change="
              key = '';
              saved = false;
            "
          >
            <option value="local">本地 BGE Reranker Base</option>
            <option value="bailian">百炼 qwen3-rerank</option>
          </select>
          <template v-if="source === 'bailian'">
            <label for="rerank-key">百炼重排 API Key</label>
            <input
              id="rerank-key"
              v-model="key"
              type="password"
              autocomplete="new-password"
              maxlength="4096"
              :required="requiredKey"
              :disabled="busy"
            />
            <p v-if="settings.bailian_key_configured">
              留空使用此库已保存的重排 Key。
            </p>
            <p>重排时，检索词和本次候选片段会发送到百炼。</p>
          </template>
          <button class="secondary" :disabled="!canSave">
            {{ busy ? "正在保存…" : "保存重排设置" }}
          </button>
        </form>
      </details>
    </template>
    <div v-if="error" role="alert">
      <p>{{ error }}</p>
      <button class="secondary" :disabled="busy" @click="read">
        刷新重排设置
      </button>
    </div>
  </section>
</template>

<style scoped>
.rerank-settings {
  border-block-end: 1px solid var(--border, #d6ddd8);
  padding-block-end: 1rem;
  margin-block-end: 1rem;
  overflow-wrap: anywhere;
}
.rerank-settings h2 {
  font-size: 1rem;
  margin: 0;
}
.rerank-settings p {
  margin: 0.4rem 0;
}
.rerank-settings details {
  margin-top: 0.7rem;
}
.rerank-settings summary {
  cursor: pointer;
  width: fit-content;
}
.rerank-settings form {
  display: grid;
  gap: 0.45rem;
  justify-items: start;
  margin-top: 0.7rem;
}
.rerank-settings select,
.rerank-settings input {
  width: min(100%, 23rem);
}
.rerank-settings select {
  min-height: 2.5rem;
  padding-inline: 0.65rem;
  border: 1px solid #b8c6bb;
  border-radius: 5px;
  background: #fff;
  color: inherit;
  font: inherit;
}
</style>
