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
  <section aria-label="项目模型配置">
    <h3>项目模型配置 · 只读</h3>
    <p>
      创建时已固定模型、部署模式和服务商。更新失效 Key
      仅作用于本项目，不会更换模型或重建索引。
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="saved" role="status">{{ saved }}</p>
    <button :disabled="busy" @click="load">刷新模型配置</button>
    <template v-if="value">
      <div
        v-for="purpose in ['embedding', 'rerank'] as const"
        :key="purpose"
        class="selection"
      >
        <h4>{{ purpose === "embedding" ? "Embedding" : "Rerank" }}</h4>
        <dl>
          <dt>部署模式 / 服务商</dt>
          <dd>
            {{ value[purpose].source === "local" ? "本地" : "云端 / 百炼" }}
          </dd>
          <dt>模型名称</dt>
          <dd>{{ value[purpose].model_name }}</dd>
          <template v-if="value[purpose].base_url"
            ><dt>服务地址</dt>
            <dd>{{ value[purpose].base_url }}</dd></template
          >
        </dl>
        <form
          v-if="value[purpose].source === 'bailian'"
          @submit.prevent="rotate(purpose)"
        >
          <label :for="`project-${purpose}-key`"
            >更新 {{ purpose }} API Key</label
          >
          <input
            :id="`project-${purpose}-key`"
            v-model="keys[purpose]"
            type="password"
            autocomplete="new-password"
            maxlength="4096"
            required
            :disabled="busy"
          />
          <button :disabled="busy || !keys[purpose]">更新此项目的 Key</button>
        </form>
      </div>
    </template>
  </section>
</template>
<style scoped>
.selection {
  margin: 16px 0;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
h4 {
  margin: 0 0 14px;
}
dd {
  margin: 4px 0 12px;
  overflow-wrap: anywhere;
}
dt {
  color: var(--ink-muted);
}
label {
  display: block;
  margin: 10px 0;
}
input {
  max-width: 100%;
  margin-bottom: 8px;
}
</style>
