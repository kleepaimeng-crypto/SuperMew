<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useProjectsStore } from "../stores/projects";
import { useAuthStore } from "../stores/auth";
import {
  endpoints,
  models,
  parseTemplates,
  type Template,
} from "../retrievalModels";

const projects = useProjectsStore(),
  auth = useAuthStore();

const items = ref<Template[]>([]),
  busy = ref(false),
  error = ref(""),
  saved = ref(false);
const editing = ref(""),
  name = ref(""),
  purpose = ref<"embedding" | "rerank">("embedding"),
  base = ref(endpoints.embedding),
  key = ref("");
let open = true;

function clear() {
  editing.value = name.value = key.value = "";
  saved.value = false;
}

watch(
  () => auth.user?.id,
  () => {
    clear();
    items.value = [];
    error.value = "";
  },
);

watch(
  purpose,
  (value) => {
    base.value = endpoints[value];
    key.value = "";
  },
  { flush: "sync" },
);

async function load() {
  busy.value = true;
  error.value = "";
  try {
    const rows = parseTemplates(
      await projects.modelRequest("/v1/retrieval-models"),
    );
    if (open) items.value = rows;
  } catch {
    if (open) error.value = "无法读取配置，请重试。";
  } finally {
    if (open) busy.value = false;
  }
}

function edit(row: Template) {
  purpose.value = row.purpose;
  editing.value = row.id;
  name.value = row.name;
  base.value = row.base_url;
  key.value = "";
  saved.value = false;
}

async function save() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  saved.value = false;
  const secret = key.value;
  key.value = "";
  try {
    await projects.modelRequest(
      `/v1/retrieval-models${editing.value ? "/" + editing.value : ""}`,
      editing.value ? "PUT" : "POST",
      {
        name: name.value.trim(),
        purpose: purpose.value,
        provider: "bailian",
        model_name: models[purpose.value],
        base_url: base.value,
        ...(secret ? { api_key: secret } : {}),
      },
    );
    if (!open) return;
    clear();
    await load();
    saved.value = !error.value;
  } catch {
    if (open)
      error.value =
        "保存未确认，请检查配置、API Key 和服务端加密设置后刷新重试。";
  } finally {
    if (open) busy.value = false;
  }
}

onMounted(load);
onBeforeUnmount(() => {
  open = false;
  key.value = "";
});
</script>

<template>
  <section class="retrieval-templates" aria-label="可复用检索模型配置">
    <div class="templates-head">
      <div>
        <h2>检索模型配置</h2>
        <p class="templates-sub">
          用于创建项目时选择。修改这里不会改变已有项目；已有项目的 Key
          在项目概览中单独更新。当前云端支持百炼 text-embedding-v4 和
          qwen3-rerank 协议，本地模型在创建项目时直接选择。
        </p>
      </div>
      <button type="button" class="secondary" :disabled="busy" @click="load">
        刷新配置
      </button>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="saved" class="notice" role="status">配置已保存。</p>

    <div class="templates-grid">
      <div class="templates-list-col">
        <h3>已保存模板（{{ items.length }}）</h3>
        <ul v-if="items.length" class="template-list">
          <li v-for="row in items" :key="row.id" class="template-card">
            <div class="template-card-info">
              <div class="template-card-title-row">
                <strong>{{ row.name }}</strong>
                <span class="purpose-pill">{{
                  row.purpose === "embedding" ? "Embedding" : "Rerank"
                }}</span>
              </div>
              <span class="template-meta">{{ row.model_name }}</span>
              <span class="template-url">{{ row.base_url }}</span>
            </div>
            <button
              type="button"
              class="secondary"
              :disabled="busy"
              @click="edit(row)"
            >
              编辑
            </button>
          </li>
        </ul>
        <p v-else class="muted">
          还没有保存云端检索模板。可在右侧新增百炼 Embedding 或 Rerank 模板。
        </p>
      </div>

      <form class="panel-card-subtle template-form" @submit.prevent="save">
        <h3>{{ editing ? "编辑可复用配置" : "新增可复用配置" }}</h3>
        <label>
          配置名称
          <input
            v-model="name"
            required
            maxlength="80"
            placeholder="例如：百炼向量模板"
            :disabled="busy"
          />
        </label>
        <label>
          用途
          <select v-model="purpose" :disabled="busy">
            <option value="embedding">Embedding</option>
            <option value="rerank">Rerank</option>
          </select>
        </label>
        <div class="readonly-pair">
          <label>
            服务商
            <input value="百炼" readonly />
          </label>
          <label>
            模型名称
            <input :value="models[purpose]" readonly />
          </label>
        </div>
        <label>
          {{ purpose === "embedding" ? "Base URL" : "Rerank Endpoint URL" }}
          <input v-model="base" required type="url" :disabled="busy" />
        </label>
        <label>
          API Key
          <input
            v-model="key"
            type="password"
            autocomplete="new-password"
            maxlength="4096"
            :required="!editing"
            :disabled="busy"
          />
        </label>
        <p v-if="editing" class="muted">
          留空保留原 Key；更改用途或端点需填写对应 Key。
        </p>
        <div class="template-form-actions">
          <button class="primary" :disabled="busy">
            {{ busy ? "正在保存…" : "保存配置" }}
          </button>
          <button
            v-if="editing"
            type="button"
            class="quiet-button"
            :disabled="busy"
            @click="clear"
          >
            取消编辑
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<style scoped>
.templates-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 18px;
}

.templates-head h2 {
  margin: 0 0 6px;
}

.templates-sub {
  margin: 0;
  font-size: 13px;
  color: var(--ink-muted);
  max-width: 78ch;
}

.templates-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 460px);
  gap: 24px;
  align-items: start;
}

.templates-list-col h3 {
  margin: 0 0 12px;
}

.template-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.template-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}

.template-card-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.template-card-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.template-card-title-row strong {
  font-size: 14px;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.purpose-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--bg-accent-soft);
  color: var(--accent-text);
}

.template-meta,
.template-url {
  font-size: 12px;
  color: var(--ink-muted);
  overflow-wrap: anywhere;
}

.template-form h3 {
  margin: 0 0 12px;
}

.template-form label {
  display: grid;
  gap: 6px;
  margin: 10px 0;
}

.readonly-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.readonly-pair label {
  margin: 4px 0;
}

.template-form-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}

@media (max-width: 1040px) {
  .templates-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .readonly-pair {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
