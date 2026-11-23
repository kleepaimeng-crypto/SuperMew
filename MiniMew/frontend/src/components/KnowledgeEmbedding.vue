<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError } from "../api";
import type { EmbeddingSettings } from "../documents";
import { useDocumentsStore } from "../stores/documents";

const props = defineProps<{ project: string | null }>();
const emit = defineEmits<{ rebuildState: [active: boolean] }>();
const documents = useDocumentsStore();
const settings = ref<EmbeddingSettings>();
const source = ref<EmbeddingSettings["current_source"]>("local");
const key = ref("");
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const rebuild = computed(() => settings.value?.rebuild);
let timer: ReturnType<typeof setTimeout> | undefined;
let open = true;

const requiredKey = computed(
  () => source.value === "bailian" && !settings.value?.bailian_key_configured,
);
const canSubmit = computed(
  () =>
    settings.value &&
    rebuild.value?.status !== "building" &&
    !loading.value &&
    !saving.value &&
    (!requiredKey.value || key.value.trim()) &&
    (source.value !== settings.value.current_source ||
      (source.value === "bailian" && key.value.trim())),
);

function apply(value: EmbeddingSettings, resetSource: boolean) {
  const finished =
    rebuild.value?.status === "building" &&
    value.rebuild?.status !== "building";
  settings.value = value;
  emit("rebuildState", value.rebuild?.status === "building");
  if (resetSource) source.value = value.current_source;
  if (finished) void documents.refresh();
  if (value.rebuild?.status === "building")
    timer = setTimeout(() => void refresh(false), 2000);
}

async function refresh(resetSource = !settings.value) {
  clearTimeout(timer);
  loading.value = true;
  error.value = "";
  try {
    const value = await documents.embeddingSettings(props.project);
    if (open) apply(value, resetSource);
  } catch {
    if (open) error.value = "暂时无法读取编码设置，请重试。";
  } finally {
    if (open) loading.value = false;
  }
}

async function start() {
  if (!canSubmit.value) return;
  saving.value = true;
  error.value = "";
  const apiKey = source.value === "bailian" ? key.value.trim() : "";
  key.value = "";
  try {
    const value = await documents.embeddingSettings(props.project, {
      source: source.value,
      ...(apiKey ? { api_key: apiKey } : {}),
    });
    if (open) apply(value, false);
  } catch (cause) {
    const code =
      cause instanceof ApiError ? await cause.errorCode() : undefined;
    if (!open) return;
    error.value =
      code === "EMBEDDING_PENDING_UPLOAD"
        ? "此知识库有文档正在构建，请等任务结束后再切换。"
        : code === "EMBEDDING_KEY_REQUIRED"
          ? "请填写百炼 API Key。"
          : code === "EMBEDDING_KEY_STORAGE_UNAVAILABLE"
            ? "服务端暂时无法保存 API Key，请检查加密主密钥是否已配置。"
            : "提交结果待确认，请刷新设置后再试。";
  } finally {
    if (open) saving.value = false;
  }
}

onMounted(() => {
  emit("rebuildState", false);
  void refresh();
});
onBeforeUnmount(() => {
  open = false;
  key.value = "";
  clearTimeout(timer);
});
</script>

<template>
  <section class="embedding-settings" aria-labelledby="embedding-heading">
    <h2 id="embedding-heading">知识库编码</h2>
    <p v-if="!settings && loading" role="status">正在读取编码设置…</p>
    <template v-if="settings">
      <p>
        当前使用 <strong>{{ settings.current_model }}</strong> ·
        {{ settings.dimensions }} 维
      </p>
      <div v-if="rebuild" class="rebuild-status" role="status">
        <template v-if="rebuild.status === 'building'">
          <p>正在重建 · {{ rebuild.target_model }}。检索继续使用当前模型。</p>
          <progress
            v-if="rebuild.total"
            :max="rebuild.total"
            :value="rebuild.completed"
            aria-label="索引重建进度"
          />
          <p v-if="rebuild.total">
            已核验 {{ rebuild.completed }} / {{ rebuild.total }} 份文档
          </p>
        </template>
        <p v-else-if="rebuild.status === 'failed'">
          切换到 {{ rebuild.target_model }} 失败，当前索引仍可用。
          {{
            rebuild.error_code?.startsWith("EMBEDDING_CREDENTIAL") ||
            rebuild.error_code === "EMBEDDING_PROVIDER_AUTH_FAILED"
              ? "请检查百炼 API Key 后重试。"
              : "请检查模型与文档状态后重试。"
          }}
        </p>
        <p v-else>索引重建完成，已切换到 {{ rebuild.target_model }}。</p>
      </div>
      <p v-if="project">
        项目模型已锁定。<RouterLink :to="`/projects/${project}`"
          >查看模型配置或更新 Key</RouterLink
        >
      </p>
      <details v-if="!project && rebuild?.status !== 'building'">
        <summary>更换编码模型</summary>
        <form @submit.prevent="start">
          <label for="embedding-source">目标模型</label>
          <select
            id="embedding-source"
            v-model="source"
            :disabled="saving"
            @change="key = ''"
          >
            <option value="local">本地 BGE Small</option>
            <option value="bailian">百炼 text-embedding-v4</option>
          </select>
          <template v-if="source === 'bailian'">
            <label for="embedding-key">百炼 API Key</label>
            <input
              id="embedding-key"
              v-model="key"
              type="password"
              autocomplete="new-password"
              maxlength="4096"
              :required="requiredKey"
              :disabled="saving"
            />
            <p v-if="settings.bailian_key_configured">
              留空将使用此知识库已保存的 Key。
            </p>
            <p>启用百炼后，此知识库的文档片段和检索词会发送到百炼。</p>
          </template>
          <button class="secondary" :disabled="!canSubmit">
            {{ saving ? "正在提交…" : "重建此知识库索引" }}
          </button>
        </form>
      </details>
    </template>
    <div v-if="error" role="alert">
      <p>{{ error }}</p>
      <button
        class="secondary"
        :disabled="loading || saving"
        @click="refresh()"
      >
        刷新设置
      </button>
    </div>
  </section>
</template>

<style scoped>
.embedding-settings {
  overflow-wrap: anywhere;
}
.embedding-settings h2 {
  font-size: 17px;
  margin: 0 0 8px;
}
.embedding-settings p {
  margin: 6px 0;
  font-size: 14px;
}
.embedding-settings a {
  color: var(--accent);
  font-weight: 500;
}
.embedding-settings a:hover {
  text-decoration: underline;
  text-underline-offset: 4px;
}
.embedding-settings details {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.embedding-settings summary {
  cursor: pointer;
  width: fit-content;
  color: var(--accent-text);
  font-size: 14px;
  font-weight: 550;
}
.embedding-settings form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}
.embedding-settings form label {
  margin: 6px 0 0;
}
.embedding-settings select,
.embedding-settings input {
  width: min(100%, 26rem);
}
.embedding-settings form button {
  align-self: flex-start;
  margin-top: 6px;
}
.rebuild-status {
  margin: 12px 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}
.rebuild-status progress {
  width: 100%;
  accent-color: var(--accent);
}
</style>
