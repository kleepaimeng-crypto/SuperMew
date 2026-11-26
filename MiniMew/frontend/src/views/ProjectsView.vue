<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useProjectsStore } from "../stores/projects";
import { useAuthStore } from "../stores/auth";
import ProjectModels from "../components/ProjectModels.vue";
import { parseTemplates, type Template } from "../retrievalModels";

const route = useRoute(),
  router = useRouter(),
  projects = useProjectsStore(),
  auth = useAuthStore();

const name = ref(""),
  content = ref(""),
  drafts = ref<Record<string, { content: string; enabled: boolean }>>({});
const templates = ref<Template[]>([]),
  templateError = ref(""),
  templateLoading = ref(true);
const embeddingChoice = ref(""),
  rerankChoice = ref(""),
  localModelsEnabled = ref(true);
const createOpen = ref(false);
let open = true;

async function loadTemplates() {
  templateLoading.value = true;
  templateError.value = "";
  try {
    const [rawTemplates, rawEmbedding] = await Promise.all([
      projects.modelRequest("/v1/retrieval-models"),
      projects
        .modelRequest("/v1/embedding-profiles/selection")
        .catch(() => null),
    ]);
    const rows = parseTemplates(rawTemplates);
    if (open) {
      templates.value = rows;
      if (
        rawEmbedding &&
        typeof rawEmbedding === "object" &&
        "local_models_enabled" in rawEmbedding &&
        typeof (rawEmbedding as Record<string, unknown>)
          .local_models_enabled === "boolean"
      ) {
        localModelsEnabled.value = Boolean(
          (rawEmbedding as Record<string, unknown>).local_models_enabled,
        );
      }
      if (!localModelsEnabled.value) {
        if (!embeddingChoice.value) {
          embeddingChoice.value =
            rows.find((t) => t.purpose === "embedding")?.id ?? "";
        }
        if (!rerankChoice.value) {
          rerankChoice.value =
            rows.find((t) => t.purpose === "rerank")?.id ?? "";
        }
      }
    }
  } catch {
    if (open) templateError.value = "无法加载模型配置，请重试。";
  } finally {
    if (open) templateLoading.value = false;
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && createOpen.value && !projects.busy) {
    createOpen.value = false;
  }
}

onMounted(() => {
  void loadTemplates();
  window.addEventListener("keydown", onKeydown);
});

const confirmRemove = (memory: (typeof projects.memories)[number]) => {
  if (
    window.confirm(
      "删除后不再用于新执行，已有Run保存的记忆快照保持不变。确认删除？",
    )
  )
    void projects.save("", memory, false, true);
};

const id = computed(() =>
  typeof route.params.projectId === "string"
    ? route.params.projectId
    : undefined,
);

watch(
  id,
  (value) => {
    content.value = "";
    drafts.value = {};
    void projects.select(value);
  },
  { immediate: true },
);

watch(
  () => auth.user?.id,
  () => {
    name.value = content.value = "";
    drafts.value = {};
    templates.value = [];
    embeddingChoice.value = rerankChoice.value = "";
    createOpen.value = false;
  },
);

watch(
  () => projects.memories,
  (rows, previous) => {
    drafts.value = Object.fromEntries(
      rows.map((r) => [
        r.id,
        previous?.find((m) => m.id === r.id)?.revision === r.revision &&
        drafts.value[r.id]
          ? drafts.value[r.id]
          : { content: r.content, enabled: r.enabled },
      ]),
    );
  },
);

void projects.load();

onBeforeUnmount(() => {
  open = false;
  window.removeEventListener("keydown", onKeydown);
  void projects.select(undefined);
});

function openCreateDrawer() {
  createOpen.value = true;
}

function closeCreateDrawer() {
  if (!projects.busy) createOpen.value = false;
}

async function create() {
  const createdId = await projects.create(name.value, {
    embedding_configuration_id: embeddingChoice.value || null,
    rerank_configuration_id: rerankChoice.value || null,
  });
  if (createdId) {
    name.value = "";
    embeddingChoice.value = "";
    rerankChoice.value = "";
    createOpen.value = false;
    await router.push(`/projects/${createdId}`);
  }
}

async function save() {
  if (await projects.save(content.value)) content.value = "";
}

function formatDate(iso?: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("zh-CN");
}
</script>

<template>
  <section class="page-container" aria-label="项目工作区">
    <header class="page-header">
      <div class="page-header-left">
        <RouterLink v-if="id" class="project-breadcrumb" to="/projects">
          ← 返回全部项目
        </RouterLink>
        <div class="page-header-title-row">
          <h1>
            {{ id && projects.selected ? projects.selected.name : "项目" }}
          </h1>
          <span v-if="!id && projects.items.length" class="scope-badge">
            {{ projects.items.length }} 个项目
          </span>
        </div>
        <p class="page-header-sub">
          {{
            id
              ? "独立项目工作区，管理固定检索模型配置与手工项目记忆。"
              : "每个项目有自己的对话、文档和手工记忆，与个人空间互相隔离。"
          }}
        </p>
      </div>
      <div class="page-header-actions">
        <template v-if="id && projects.selected">
          <RouterLink
            class="btn-link primary"
            :to="{ path: '/threads', query: { project: id } }"
          >
            打开项目对话
          </RouterLink>
          <RouterLink
            class="btn-link secondary"
            :to="{ path: '/documents', query: { project: id } }"
          >
            管理项目文档
          </RouterLink>
        </template>
        <button
          v-if="!id"
          class="secondary"
          :disabled="projects.loading"
          @click="projects.load()"
        >
          刷新项目
        </button>
        <button
          type="button"
          :class="id ? 'secondary' : 'primary'"
          @click="openCreateDrawer"
        >
          ＋ 新建项目
        </button>
      </div>
    </header>

    <div v-if="projects.error" role="alert" class="project-error">
      <p>{{ projects.error }}</p>
      <button
        class="secondary"
        @click="id ? projects.select(id) : projects.load()"
      >
        重新加载
      </button>
    </div>

    <!-- 项目列表卡片网格 -->
    <template v-if="!id">
      <div v-if="projects.items.length" class="project-grid-wrap">
        <div class="project-card-grid" aria-label="我的项目">
          <article
            v-for="project in projects.items"
            :key="project.id"
            class="panel-card project-card"
          >
            <div class="project-card-head">
              <RouterLink
                class="project-card-title"
                :to="`/projects/${project.id}`"
              >
                {{ project.name }}
              </RouterLink>
              <span v-if="project.created_at" class="project-card-date">
                创建于 {{ formatDate(project.created_at) }}
              </span>
            </div>
            <p class="project-card-desc">
              独立知识库、固定检索模型与手工记忆空间。
            </p>
            <div class="project-card-actions">
              <RouterLink
                class="project-chip primary-chip"
                :to="{ path: '/threads', query: { project: project.id } }"
              >
                进入对话
              </RouterLink>
              <RouterLink
                class="project-chip"
                :to="{ path: '/documents', query: { project: project.id } }"
              >
                项目文档
              </RouterLink>
              <RouterLink class="project-chip" :to="`/projects/${project.id}`">
                概览与记忆
              </RouterLink>
            </div>
          </article>
        </div>

        <button
          v-if="projects.nextOffset !== null"
          class="secondary load-more-projects"
          :disabled="projects.loading"
          @click="projects.load(true)"
        >
          {{ projects.loading ? "正在加载…" : "加载更多项目" }}
        </button>
      </div>

      <div
        v-else-if="!projects.loading"
        class="empty-state-card project-empty-card"
      >
        <h2>为一项工作建立独立空间</h2>
        <p>
          项目内的多段对话共用这个项目的资料和记忆。普通对话继续使用原来的个人知识库。
        </p>
        <div class="empty-state-actions">
          <button type="button" class="primary" @click="openCreateDrawer">
            ＋ 新建第一个项目
          </button>
          <RouterLink class="btn-link secondary" to="/threads">
            进入普通对话
          </RouterLink>
        </div>
      </div>

      <p v-else role="status" class="muted">正在读取项目列表…</p>
    </template>

    <!-- 项目详情双栏工作台 -->
    <template v-else>
      <div v-if="projects.selected" class="project-detail-grid">
        <div class="panel-card project-models-col">
          <ProjectModels
            :key="`${auth.user?.id}:${projects.selected.id}`"
            :project="projects.selected.id"
          />
        </div>

        <section class="panel-card project-memories-col" aria-label="项目记忆">
          <div class="memories-header">
            <div>
              <h2>项目记忆</h2>
              <p class="memories-desc">
                由你明确保存，同项目的新对话可以读取。修改不影响已开始的执行；文档下线不会自动删除你保存的记忆。
              </p>
              <p class="project-note">
                最多启用 20 条，每条不超过 500
                字符。每轮固定本次采用的内容与版本，不自动提取聊天。
              </p>
            </div>
            <span class="scope-badge">
              {{ projects.memories.length }} 条记忆
            </span>
          </div>

          <form class="memory-create-form" @submit.prevent="save">
            <label for="memory-content">保存重要信息</label>
            <textarea
              id="memory-content"
              v-model="content"
              maxlength="500"
              rows="3"
              required
              :disabled="projects.busy"
              placeholder="例如：实验只使用 2024 年以前的数据。"
            ></textarea>
            <div class="memory-create-bar">
              <span class="muted">{{ content.length }} / 500</span>
              <button
                class="primary"
                :disabled="projects.busy || !content.trim()"
              >
                保存项目记忆
              </button>
            </div>
          </form>

          <p v-if="!projects.memories.length" class="muted memory-empty">
            暂未保存记忆。添加后将自动注入本项目后续对话上下文。
          </p>

          <div v-else class="memory-grid">
            <form
              v-for="memory in projects.memories"
              :key="memory.id"
              class="memory-item"
              @submit.prevent="
                projects.save(
                  drafts[memory.id]!.content,
                  memory,
                  drafts[memory.id]!.enabled,
                )
              "
            >
              <template v-if="drafts[memory.id]">
                <div class="memory-item-head">
                  <label :for="`memory-${memory.id}`">
                    手工记忆 · 版本 {{ memory.revision }}
                  </label>
                  <label class="memory-toggle">
                    <input
                      v-model="drafts[memory.id]!.enabled"
                      type="checkbox"
                      :disabled="projects.busy"
                    />
                    用于后续对话
                  </label>
                </div>
                <textarea
                  :id="`memory-${memory.id}`"
                  v-model="drafts[memory.id]!.content"
                  maxlength="500"
                  rows="3"
                  required
                  :disabled="projects.busy"
                ></textarea>
                <div class="memory-actions">
                  <button class="secondary" :disabled="projects.busy">
                    保存修改
                  </button>
                  <button
                    type="button"
                    class="quiet-button danger-quiet"
                    :disabled="projects.busy"
                    @click="confirmRemove(memory)"
                  >
                    删除记忆
                  </button>
                </div>
              </template>
            </form>
          </div>
        </section>
      </div>
      <p v-else-if="!projects.error" role="status" class="muted">
        正在读取项目…
      </p>
    </template>

    <!-- 新建项目抽屉 -->
    <div
      v-if="createOpen"
      class="drawer-backdrop"
      @click.self="closeCreateDrawer"
    >
      <div
        class="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
      >
        <div class="drawer-header">
          <h2 id="create-project-title">新建项目</h2>
          <button
            type="button"
            class="quiet-button"
            :disabled="projects.busy"
            aria-label="关闭新建项目面板"
            @click="closeCreateDrawer"
          >
            关闭
          </button>
        </div>

        <form class="drawer-form" @submit.prevent="create">
          <div class="drawer-body">
            <p class="drawer-lead">
              为项目绑定专属知识库和固定检索模型。创建后即可在独立空间内开展对话与管理资料。
            </p>

            <label for="project-name">新项目名称</label>
            <input
              id="project-name"
              v-model="name"
              maxlength="100"
              required
              placeholder="输入项目名称，例如：研发知识库"
              :disabled="projects.busy"
            />

            <label for="project-embedding">Embedding 模型</label>
            <select
              id="project-embedding"
              v-model="embeddingChoice"
              :disabled="projects.busy || templateLoading"
            >
              <option v-if="localModelsEnabled" value="">
                本地 BAAI/bge-small-zh-v1.5
              </option>
              <option v-else value="" disabled>
                请选择云端 Embedding 配置
              </option>
              <option
                v-for="item in templates.filter(
                  (t) => t.purpose === 'embedding',
                )"
                :key="item.id"
                :value="item.id"
              >
                {{ item.name }} · {{ item.model_name }}
              </option>
            </select>

            <label for="project-rerank">Rerank 模型</label>
            <select
              id="project-rerank"
              v-model="rerankChoice"
              :disabled="projects.busy || templateLoading"
            >
              <option v-if="localModelsEnabled" value="">
                本地 BAAI/bge-reranker-base
              </option>
              <option v-else value="" disabled>请选择云端 Rerank 配置</option>
              <option
                v-for="item in templates.filter((t) => t.purpose === 'rerank')"
                :key="item.id"
                :value="item.id"
              >
                {{ item.name }} · {{ item.model_name }}
              </option>
            </select>

            <p v-if="!localModelsEnabled" class="project-note drawer-note">
              当前服务端已关闭本地模型，请先在<RouterLink
                class="text-link"
                to="/account"
                @click="closeCreateDrawer"
                >账户设置</RouterLink
              >中保存云端 Embedding 与 Rerank 配置模板。
            </p>
            <p class="project-note drawer-note">
              创建后模型与部署模式不可更换。云端配置可在<RouterLink
                class="text-link"
                to="/account"
                @click="closeCreateDrawer"
                >账户设置</RouterLink
              >中保存复用；云端模式会向所选服务发送待编码文本或检索候选。
            </p>

            <div v-if="templateError" class="project-error" role="alert">
              <p>{{ templateError }}</p>
              <button type="button" class="secondary" @click="loadTemplates">
                重试加载配置
              </button>
            </div>
          </div>

          <div class="drawer-footer">
            <button
              type="button"
              class="secondary"
              :disabled="projects.busy"
              @click="closeCreateDrawer"
            >
              取消
            </button>
            <button
              type="submit"
              class="primary"
              :disabled="
                projects.busy ||
                !name.trim() ||
                templateLoading ||
                !!templateError ||
                (!localModelsEnabled && (!embeddingChoice || !rerankChoice))
              "
            >
              {{ projects.busy ? "正在创建…" : "新建项目" }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.project-breadcrumb {
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  margin-bottom: 2px;
}

.project-breadcrumb:hover {
  text-decoration: underline;
  text-underline-offset: 4px;
}

.project-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 18px;
}

.project-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 22px 24px;
  transition:
    border-color 0.15s,
    box-shadow 0.15s;
}

.project-card:hover {
  border-color: var(--accent-border);
  box-shadow: var(--shadow-sm);
}

.project-card-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.project-card-title {
  font-size: 17px;
  font-weight: 650;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.project-card-title:hover {
  color: var(--accent);
}

.project-card-date {
  font-size: 12px;
  color: var(--ink-muted);
}

.project-card-desc {
  margin: 0;
  font-size: 13px;
  color: var(--ink-muted);
  line-height: 1.55;
}

.project-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid #eef2f8;
}

.project-chip {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 11px;
  border-radius: 7px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  color: var(--ink-secondary);
  font-size: 12px;
  font-weight: 500;
  transition:
    background 0.12s,
    border-color 0.12s,
    color 0.12s;
}

.project-chip:hover {
  background: var(--bg-accent-softer);
  border-color: var(--accent-border);
  color: var(--accent-text);
}

.project-chip.primary-chip {
  background: var(--bg-accent-softer);
  border-color: var(--accent-border);
  color: var(--accent-text);
}

.project-chip.primary-chip:hover {
  background: var(--bg-accent-soft);
}

.load-more-projects {
  margin-top: 20px;
}

.project-detail-grid {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.memories-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
}

.memories-header h2 {
  margin: 0 0 6px;
}

.memories-desc {
  margin: 0 0 6px;
  font-size: 14px;
}

.project-note {
  font-size: 13px;
  color: var(--ink-muted);
  margin: 0;
}

.memory-create-form {
  padding: 16px 18px;
  border-radius: 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  margin-bottom: 20px;
}

.memory-create-form label {
  margin: 0 0 8px;
  font-weight: 600;
  color: var(--ink);
}

.memory-create-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
}

textarea {
  width: 100%;
  padding: 10px 12px;
  background: var(--bg-canvas);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  color: var(--ink);
  resize: vertical;
  min-height: 84px;
}

textarea:focus-visible {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
  outline: none;
}

.memory-empty {
  margin: 6px 0 0;
}

.memory-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.memory-item {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-subtle);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.memory-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.memory-item-head > label:first-child {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-secondary);
}

.memory-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  cursor: pointer;
}

.memory-toggle input {
  width: 15px;
  height: 15px;
  flex: 0 0 15px;
  padding: 0;
  margin: 0;
  accent-color: var(--accent);
}

.memory-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.danger-quiet:hover:not(:disabled) {
  background: var(--danger-bg);
  color: var(--danger);
}

.project-error {
  border: 1px solid var(--danger-border);
  background: var(--danger-bg);
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.project-error p {
  margin: 0;
  color: var(--danger);
  font-size: 13px;
}

.drawer-form {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.drawer-lead {
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--ink-muted);
}

.drawer-note {
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  line-height: 1.6;
}

@media (max-width: 1080px) {
  .project-detail-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .project-card-grid,
  .memory-grid {
    grid-template-columns: 1fr;
  }
}
</style>
