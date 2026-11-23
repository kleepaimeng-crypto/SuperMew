<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useProjectsStore } from "../stores/projects";
import { parseProjectModels, type ProjectModels } from "../retrievalModels";

const props = defineProps<{ project: string }>();
const projects = useProjectsStore(),
  value = ref<ProjectModels>(),
  busy = ref(false),
  error = ref(""),
  saved = ref("");
const keys = ref({ embedding: "", rerank: "" });
let open = true;

async function load() {
  busy.value = true;
  error.value = "";
  try {
    const result = parseProjectModels(
      await projects.modelRequest(`/v1/projects/${props.project}/models`),
    );
    if (open) value.value = result;
  } catch {
    if (open) error.value = "无法读取项目模型配置，请重试。";
  } finally {
    if (open) busy.value = false;
  }
}

async function rotate(purpose: "embedding" | "rerank") {
  const key = keys.value[purpose];
  keys.value[purpose] = "";
  busy.value = true;
  error.value = saved.value = "";
  try {
    const result = parseProjectModels(
      await projects.modelRequest(
        `/v1/projects/${props.project}/models/${purpose}/credential`,
        "PUT",
        { api_key: key },
      ),
    );
    if (open) {
      value.value = result;
      saved.value = "已更新本项目的 API Key，其他项目与已有执行保持原绑定。";
    }
  } catch {
    if (open) error.value = "Key 更新未确认，请刷新配置后重试。";
  } finally {
    if (open) busy.value = false;
  }
}

onMounted(load);
onBeforeUnmount(() => {
  open = false;
  keys.value = { embedding: "", rerank: "" };
});
</script>

<template>
  <section class="project-models" aria-label="项目模型配置">
    <div class="models-head">
      <div>
        <h2>项目模型配置 · 只读</h2>
        <p class="models-sub">
          创建时已固定模型、部署模式和服务商。更新失效 Key
          仅作用于本项目，不会更换模型或重建索引。
        </p>
      </div>
      <button type="button" class="quiet-button" :disabled="busy" @click="load">
        刷新模型配置
      </button>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="saved" class="notice" role="status">{{ saved }}</p>

    <div v-if="value" class="selection-list">
      <div
        v-for="purpose in ['embedding', 'rerank'] as const"
        :key="purpose"
        class="selection-card"
      >
        <div class="selection-card-head">
          <h3>{{ purpose === "embedding" ? "Embedding" : "Rerank" }}</h3>
          <span
            class="source-pill"
            :class="value[purpose].source === 'local' ? 'is-local' : 'is-cloud'"
          >
            {{ value[purpose].source === "local" ? "本地" : "云端 / 百炼" }}
          </span>
        </div>
        <dl class="selection-dl">
          <div>
            <dt>部署模式 / 服务商</dt>
            <dd>
              {{ value[purpose].source === "local" ? "本地" : "云端 / 百炼" }}
            </dd>
          </div>
          <div>
            <dt>模型名称</dt>
            <dd>{{ value[purpose].model_name }}</dd>
          </div>
          <div v-if="value[purpose].base_url">
            <dt>服务地址</dt>
            <dd>{{ value[purpose].base_url }}</dd>
          </div>
        </dl>
        <form
          v-if="value[purpose].source === 'bailian'"
          class="rotate-form"
          @submit.prevent="rotate(purpose)"
        >
          <label :for="`project-${purpose}-key`">
            更新 {{ purpose }} API Key
          </label>
          <input
            :id="`project-${purpose}-key`"
            v-model="keys[purpose]"
            type="password"
            autocomplete="new-password"
            maxlength="4096"
            required
            placeholder="输入新的 API Key"
            :disabled="busy"
          />
          <button
            type="submit"
            class="secondary"
            :disabled="busy || !keys[purpose]"
          >
            更新此项目的 Key
          </button>
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.models-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}

.models-head button {
  white-space: nowrap;
  flex-shrink: 0;
}

.models-head h2 {
  margin: 0 0 6px;
}

.models-sub {
  margin: 0;
  font-size: 13px;
  color: var(--ink-muted);
}

.selection-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.selection-card {
  padding: 16px;
  border-radius: 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}

.selection-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.selection-card-head h3 {
  margin: 0;
  font-size: 15px;
}

.source-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
}

.source-pill.is-local {
  background: #e2e8f0;
  color: #334155;
}

.source-pill.is-cloud {
  background: var(--bg-accent-soft);
  color: var(--accent-text);
}

.selection-dl {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selection-dl div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.selection-dl dt {
  font-size: 12px;
  color: var(--ink-muted);
}

.selection-dl dd {
  margin: 0;
  font-size: 13px;
  font-weight: 550;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.rotate-form {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.rotate-form label {
  margin: 0 0 6px;
  font-size: 12px;
}

.rotate-form input {
  margin-bottom: 10px;
}
</style>
