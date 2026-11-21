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
  <section aria-label="可复用检索模型配置">
    <h2>检索模型配置</h2>
    <p>
      用于创建项目时选择。修改这里不会改变已有项目；已有项目的 Key
      在项目概览中单独更新。
    </p>
    <p>
      当前云端支持百炼 text-embedding-v4 和 qwen3-rerank
      协议，本地模型在创建项目时直接选择。
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="saved" role="status">配置已保存。</p>
    <button type="button" :disabled="busy" @click="load">刷新配置</button>
    <ul>
      <li v-for="row in items" :key="row.id">
        {{ row.name }} · {{ row.model_name }}
        <button :disabled="busy" @click="edit(row)">编辑</button>
      </li>
    </ul>
    <form @submit.prevent="save">
      <h3>{{ editing ? "编辑可复用配置" : "新增可复用配置" }}</h3>
      <label
        >配置名称<input v-model="name" required maxlength="80" :disabled="busy"
      /></label>
      <label
        >用途<select v-model="purpose" :disabled="busy">
          <option value="embedding">Embedding</option>
          <option value="rerank">Rerank</option>
        </select></label
      >
      <label>服务商<input value="百炼" readonly /></label>
      <label>模型名称<input :value="models[purpose]" readonly /></label>
      <label
        >{{ purpose === "embedding" ? "Base URL" : "Rerank Endpoint URL"
        }}<input v-model="base" required type="url" :disabled="busy"
      /></label>
      <label
        >API Key<input
          v-model="key"
          type="password"
          autocomplete="new-password"
          maxlength="4096"
          :required="!editing"
          :disabled="busy"
      /></label>
      <p v-if="editing">留空保留原 Key；更改用途或端点需填写对应 Key。</p>
      <button class="primary" :disabled="busy">
        {{ busy ? "正在保存…" : "保存配置" }}
      </button>
      <button v-if="editing" type="button" :disabled="busy" @click="clear">
        取消编辑
      </button>
    </form>
  </section>
</template>
<style scoped>
label {
  display: grid;
  gap: 6px;
  margin: 12px 0;
}
form {
  max-width: 620px;
}
input,
select {
  width: 100%;
}
li {
  margin: 12px 0;
  overflow-wrap: anywhere;
}
</style>
