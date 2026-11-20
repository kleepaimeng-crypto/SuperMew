<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ApiError, request } from "../api";
import { useAuthStore } from "../stores/auth";
import type { components } from "../generated/api";

type Settings = components["schemas"]["AccountSettingsView"];
type Model = components["schemas"]["ModelView"];
type Preset = components["schemas"]["PresetView"];
const auth = useAuthStore();
const base = "/v1/account/model-settings";
const data = ref<Settings | null>(null),
  presets = ref<Preset[]>([]);
const loading = ref(false),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const editing = ref<string | null>(null),
  formOpen = ref(false),
  deleting = ref<string | null>(null);
const adapter = ref("deepseek"),
  modelName = ref(""),
  baseUrl = ref(""),
  name = ref("");
// Key仅在此临时表单内存在，不进入Pinia、URL或浏览器持久存储。
const apiKey = ref(""),
  maxTokens = ref(4096),
  timeout = ref(120),
  temperature = ref(0.2),
  knowledge = ref(true);
let epoch = 0;
let controller = new AbortController();
const selectedPreset = computed(() =>
  presets.value.find((p) => p.id === adapter.value),
);
const current = computed(() =>
  data.value?.models.find((m) => m.id === data.value?.current_model_id),
);
const valid = (e: number) => e === epoch && auth.status === "authenticated";

function parseSettings(value: unknown): Settings {
  const v = value as Settings;
  if (
    !v ||
    !Array.isArray(v.models) ||
    typeof v.knowledge_enabled !== "boolean" ||
    typeof v.knowledge_available !== "boolean" ||
    typeof v.credential_storage_ready !== "boolean" ||
    typeof v.default_model_name !== "string" ||
    typeof v.default_configured !== "boolean" ||
    (v.current_model_id !== null && typeof v.current_model_id !== "string") ||
    v.models.some(
      (m) =>
        !m ||
        typeof m.id !== "string" ||
        typeof m.name !== "string" ||
        typeof m.model_name !== "string" ||
        typeof m.adapter !== "string" ||
        typeof m.base_url !== "string" ||
        typeof m.key_configured !== "boolean" ||
        !Number.isInteger(m.revision) ||
        !Number.isInteger(m.max_output_tokens) ||
        typeof m.timeout_seconds !== "number" ||
        typeof m.temperature !== "number",
    )
  )
    throw new Error("Invalid model settings");
  return v;
}
async function call(path = "", method = "GET", body?: unknown) {
  const e = epoch,
    owner = auth.user?.id,
    token = auth.accessToken;
  if (!owner || !token) throw new Error("Identity unavailable");
  try {
    return await request(base + path, method, body, token, controller.signal);
  } catch (cause) {
    if (
      cause instanceof ApiError &&
      cause.status === 401 &&
      valid(e) &&
      owner === auth.user?.id
    ) {
      const refreshed = await auth.refreshAccess(token);
      if (refreshed && valid(e) && owner === auth.user?.id && auth.accessToken)
        return request(
          base + path,
          method,
          body,
          auth.accessToken,
          controller.signal,
        );
    }
    throw cause;
  }
}
async function load() {
  const e = epoch;
  loading.value = true;
  error.value = "";
  try {
    const [settings, catalog] = await Promise.all([call(), call("/presets")]);
    if (!valid(e)) return;
    const parsed = parseSettings(settings);
    if (
      !Array.isArray(catalog) ||
      catalog.some(
        (p) =>
          !p ||
          typeof p.id !== "string" ||
          typeof p.name !== "string" ||
          typeof p.base_url !== "string" ||
          !Array.isArray(p.models) ||
          p.models.some((m: unknown) => typeof m !== "string"),
      )
    )
      throw new Error("Invalid model presets");
    presets.value = catalog;
    data.value = parsed;
    knowledge.value = data.value.knowledge_enabled;
  } catch {
    if (valid(e)) error.value = "模型设置加载失败，请重试。";
  } finally {
    if (valid(e)) loading.value = false;
  }
}
function resetForm() {
  editing.value = null;
  formOpen.value = false;
  apiKey.value = "";
  name.value = "";
  modelName.value = "";
  baseUrl.value = "";
  maxTokens.value = 4096;
  timeout.value = 120;
  temperature.value = 0.2;
}
function choosePreset() {
  modelName.value = selectedPreset.value?.models[0] ?? "";
  baseUrl.value = selectedPreset.value?.base_url ?? "";
  apiKey.value = "";
}
function open(model?: Model) {
  resetForm();
  deleting.value = null;
  notice.value = "";
  formOpen.value = true;
  if (model) {
    editing.value = model.id;
    adapter.value = model.adapter;
    name.value = model.name;
    modelName.value = model.model_name;
    baseUrl.value = model.base_url;
    maxTokens.value = model.max_output_tokens;
    timeout.value = model.timeout_seconds;
    temperature.value = model.temperature;
  } else {
    adapter.value = "deepseek";
    choosePreset();
  }
}
async function mutate(operation: () => Promise<unknown>, message: string) {
  if (busy.value) return;
  const e = epoch;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await operation();
    if (!valid(e)) return;
    resetForm();
    deleting.value = null;
    notice.value = message;
    await load();
  } catch (cause) {
    if (!valid(e)) return;
    error.value =
      cause instanceof ApiError && cause.status === 422
        ? "配置未保存，请检查接口地址、模型名称和密钥。"
        : cause instanceof ApiError && cause.status === 503
          ? "密钥存储暂不可用，请联系维护者检查后重试。"
          : "操作结果尚未确认，请刷新设置后检查，再决定是否重试。";
  } finally {
    if (valid(e)) {
      apiKey.value = "";
      busy.value = false;
    }
  }
}
function save() {
  const body = {
    name:
      name.value.trim() ||
      `${selectedPreset.value?.name ?? "自定义"} · ${modelName.value}`.slice(
        0,
        80,
      ),
    adapter: adapter.value,
    model_name: modelName.value,
    base_url: baseUrl.value || null,
    api_key: apiKey.value || null,
    max_output_tokens: maxTokens.value,
    timeout_seconds: timeout.value,
    temperature: temperature.value,
  };
  void mutate(
    () =>
      call(
        editing.value ? `/models/${editing.value}` : "/models",
        editing.value ? "PUT" : "POST",
        body,
      ),
    "配置已保存，尚未发起模型调用。",
  );
}
function select(modelId: string | null) {
  void mutate(
    () =>
      call("/selection", "PUT", {
        model_id: modelId,
        knowledge_enabled: knowledge.value,
      }),
    "使用设置已更新，将用于之后的新任务。",
  );
}
watch(
  () => auth.user?.id,
  () => {
    epoch++;
    controller.abort();
    controller = new AbortController();
    resetForm();
    data.value = null;
    presets.value = [];
    error.value = notice.value = "";
    loading.value = busy.value = false;
    deleting.value = null;
    if (auth.user) void load();
  },
  { immediate: true, flush: "sync" },
);
onBeforeUnmount(() => {
  epoch++;
  controller.abort();
  apiKey.value = "";
});
</script>

<template>
  <section class="model-settings" aria-labelledby="models-heading">
    <div class="settings-heading">
      <h2 id="models-heading">模型与能力</h2>
      <button class="secondary" :disabled="busy || loading" @click="load">
        刷新设置
      </button>
    </div>
    <p class="settings-description">
      为新任务选择模型。已经创建的任务继续使用原配置。
    </p>
    <p v-if="loading && !data" role="status">正在读取模型设置…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="data">
      <p class="current-model">
        当前使用：<strong>{{
          current?.name ?? `服务端默认 · ${data.default_model_name}`
        }}</strong>
      </p>
      <p
        v-if="!current && !data.default_configured"
        class="settings-description"
      >
        服务端默认模型尚未配置密钥，请添加自己的模型配置。
      </p>
      <ul v-if="data.models.length" class="model-list">
        <li v-for="model in data.models" :key="model.id">
          <div>
            <strong>{{ model.name }}</strong
            ><span class="model-detail"
              >{{ model.model_name }} ·
              {{ model.key_configured ? "密钥已保存" : "未设置密钥" }}</span
            >
          </div>
          <div class="model-actions">
            <button
              class="secondary"
              :disabled="busy || loading || model.id === data.current_model_id"
              @click="select(model.id)"
            >
              {{ model.id === data.current_model_id ? "当前使用" : "设为当前" }}
            </button>
            <button
              class="quiet-button"
              :disabled="busy || loading"
              @click="open(model)"
            >
              编辑
            </button>
            <button
              class="quiet-button"
              :disabled="busy || loading"
              @click="deleting = model.id"
            >
              删除
            </button>
          </div>
          <div v-if="deleting === model.id" class="delete-model">
            <p>删除会撤销这份配置的密钥，已有任务将无法再用它继续调用。</p>
            <button
              class="secondary"
              :disabled="busy"
              @click="
                mutate(
                  () => call(`/models/${model.id}`, 'DELETE'),
                  '配置已删除，密钥已撤销。',
                )
              "
            >
              确认删除
            </button>
            <button
              class="quiet-button"
              :disabled="busy"
              @click="deleting = null"
            >
              保留配置
            </button>
          </div>
        </li>
      </ul>
      <p v-else class="settings-description">
        还没有个人模型配置，当前沿用服务端默认。
      </p>
      <div class="model-actions">
        <button
          :disabled="busy || loading || !data.credential_storage_ready"
          @click="open()"
        >
          添加模型配置
        </button>
        <button
          v-if="current"
          class="secondary"
          :disabled="busy || loading"
          @click="select(null)"
        >
          恢复服务端默认
        </button>
      </div>
      <p v-if="!data.credential_storage_ready" class="settings-description">
        密钥存储尚未就绪，请联系维护者完成配置。
      </p>
      <form v-if="formOpen" class="model-form" @submit.prevent="save">
        <h3>{{ editing ? "编辑模型配置" : "添加模型配置" }}</h3>
        <fieldset :disabled="busy || loading">
          <label for="model-vendor">服务商</label>
          <select id="model-vendor" v-model="adapter" @change="choosePreset">
            <option v-for="item in presets" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
            <option value="generic">通用 OpenAI 兼容服务</option>
          </select>
          <label for="model-id">模型</label>
          <select v-if="selectedPreset" id="model-id" v-model="modelName">
            <option
              v-for="model in selectedPreset.models"
              :key="model"
              :value="model"
            >
              {{ model }}
            </option>
          </select>
          <input
            v-else
            id="model-id"
            v-model="modelName"
            required
            maxlength="200"
            placeholder="填写服务商提供的模型 API ID"
          />
          <label for="model-key"
            >API Key{{ editing ? "（留空保留原密钥）" : "" }}</label
          >
          <input
            id="model-key"
            v-model="apiKey"
            type="password"
            :required="!editing"
            maxlength="4096"
            autocomplete="off"
            spellcheck="false"
          />
          <p class="field-hint">密钥加密保存，之后不会显示原值。</p>
          <div v-if="adapter === 'generic'" class="endpoint-field">
            <label for="generic-endpoint">Base URL</label
            ><input
              id="generic-endpoint"
              v-model="baseUrl"
              required
              type="url"
              placeholder="https://api.example.com/v1"
              maxlength="2048"
            />
          </div>
          <details class="advanced-model">
            <summary>更多设置</summary>
            <label for="model-label">配置名称（可选）</label
            ><input id="model-label" v-model="name" maxlength="80" />
            <template v-if="adapter === 'qwen'"
              ><label for="qwen-endpoint">接口地址</label
              ><input
                id="qwen-endpoint"
                v-model="baseUrl"
                type="url"
                required
              />
              <p class="field-hint">
                使用其他地域或业务空间时，填写与密钥匹配的地址。
              </p></template
            >
            <template v-if="adapter !== 'kimi'">
              <label for="model-temperature">Temperature</label>
              <input
                id="model-temperature"
                v-model.number="temperature"
                type="number"
                min="0"
                max="2"
                step="0.1"
                required
              />
            </template>
            <div class="model-numbers">
              <div>
                <label for="model-tokens">最大输出 Token</label
                ><input
                  id="model-tokens"
                  v-model.number="maxTokens"
                  type="number"
                  min="1"
                  max="65536"
                  required
                />
              </div>
              <div>
                <label for="model-timeout">任务期限（秒）</label
                ><input
                  id="model-timeout"
                  v-model.number="timeout"
                  type="number"
                  min="1"
                  max="300"
                  required
                />
              </div>
            </div>
          </details>
          <div class="model-actions">
            <button type="submit">{{ busy ? "正在保存…" : "保存配置" }}</button
            ><button type="button" class="quiet-button" @click="resetForm">
              取消编辑
            </button>
          </div>
        </fieldset>
      </form>
      <section class="capability-setting" aria-labelledby="knowledge-heading">
        <h3 id="knowledge-heading">知识检索</h3>
        <label class="checkbox-label"
          ><input
            v-model="knowledge"
            type="checkbox"
            :disabled="busy || loading"
          />允许新任务检索当前范围的资料</label
        >
        <p class="settings-description">
          {{
            data.knowledge_available
              ? "知识服务已启用，检索范围仍由当前对话所属空间决定。"
              : "服务端尚未启用知识服务；此处保留你的使用偏好。"
          }}
        </p>
        <button
          class="secondary"
          :disabled="busy || loading || knowledge === data.knowledge_enabled"
          @click="select(data.current_model_id)"
        >
          保存使用设置
        </button>
      </section>
    </template>
  </section>
</template>

<style scoped>
.model-settings {
  margin-top: 40px;
  padding-top: 28px;
  border-top: 1px solid #dce3d8;
}
.settings-heading,
.model-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.settings-heading {
  justify-content: space-between;
  gap: 16px;
}
h2 {
  font-size: 24px;
  margin: 0;
}
h3 {
  font-size: 18px;
  margin: 0 0 16px;
}
.settings-description,
.field-hint,
.model-detail {
  color: #536c5d;
  font-size: 14px;
  line-height: 1.65;
}
.model-list strong {
  overflow-wrap: anywhere;
}
.current-model {
  margin: 24px 0 16px;
  overflow-wrap: anywhere;
}
.model-list {
  list-style: none;
  padding: 0;
  margin: 0 0 20px;
}
.model-list li {
  padding: 18px 0;
  border-top: 1px solid #dce3d8;
}
.model-detail {
  display: block;
  overflow-wrap: anywhere;
  margin: 4px 0 10px;
}
.model-form {
  margin-top: 28px;
  padding: 24px 0;
  border-top: 1px solid #dce3d8;
}
fieldset {
  min-width: 0;
  border: 0;
  padding: 0;
  margin: 0;
}
label {
  display: block;
  margin: 16px 0 7px;
  font-size: 14px;
  font-weight: 600;
}
select {
  width: 100%;
  min-height: 49px;
  border: 1px solid #c4cebf;
  border-radius: 6px;
  padding: 10px 12px;
  background: #fcfdf9;
  color: #294e39;
  font: inherit;
}
select:focus-visible,
summary:focus-visible {
  outline: 2px solid #3f6a50;
  outline-offset: 3px;
}
.field-hint {
  margin: 6px 0 16px;
}
input::placeholder {
  color: #536c5d;
}
.advanced-model {
  margin: 22px 0;
}
summary {
  cursor: pointer;
  padding: 8px 0;
  color: #294e39;
}
.model-numbers {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.capability-setting {
  margin-top: 32px;
  padding-top: 24px;
  border-top: 1px solid #dce3d8;
}
.checkbox-label {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  line-height: 1.6;
}
.checkbox-label input {
  width: 18px;
  min-height: 18px;
  height: 18px;
  margin: 3px 0 0;
  accent-color: #294e39;
  flex-shrink: 0;
}
.delete-model {
  margin-top: 12px;
  color: #733f30;
  line-height: 1.6;
}
.delete-model button {
  margin-right: 10px;
}
button {
  min-height: 44px;
}
@media (max-width: 520px) {
  .model-numbers {
    grid-template-columns: 1fr;
    gap: 0;
  }
  .settings-heading {
    align-items: flex-start;
  }
}
</style>
