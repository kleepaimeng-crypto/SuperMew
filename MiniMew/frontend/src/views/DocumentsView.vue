<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import KnowledgeEmbedding from "../components/KnowledgeEmbedding.vue";
import KnowledgeRerank from "../components/KnowledgeRerank.vue";
import { activeJob, jobError, jobLabel } from "../documents";
import { useDocumentsStore } from "../stores/documents";
import { useAuthStore } from "../stores/auth";
import { useWorkspaceStore } from "../stores/workspace";

const route = useRoute(),
  router = useRouter(),
  documents = useDocumentsStore(),
  auth = useAuthStore(),
  workspace = useWorkspaceStore();

const file = ref<File | null>(null),
  fileInput = ref<HTMLInputElement>(),
  rebuilding = ref(false),
  activeTab = ref<"documents" | "models">("documents");

const documentId = computed(() =>
  typeof route.params.documentId === "string"
    ? route.params.documentId
    : undefined,
);
const projectId = computed(() =>
  typeof route.query.project === "string" ? route.query.project : null,
);
const showArchived = computed(() => route.query.archived === "1");
const projectQuery = computed(() => ({
  ...(projectId.value ? { project: projectId.value } : {}),
  ...(showArchived.value ? { archived: "1" } : {}),
}));

async function withdraw() {
  if (window.confirm("下线后不再用于新检索，历史版本与引用仍保留。确认下线？"))
    await documents.withdraw();
}

watch([projectId, showArchived], () => {
  file.value = null;
  if (fileInput.value) fileInput.value.value = "";
  documents.setScope(projectId.value, showArchived.value);
  documents.activate();
  void documents.loadList(true);
  void documents.selectDocument(documentId.value);
});

watch(
  () => auth.user?.id,
  () => {
    file.value = null;
    if (fileInput.value) fileInput.value.value = "";
    activeTab.value = "documents";
  },
);

const job = computed(() => documents.selected?.latest_job);
const profile = computed(
  () => documents.latestVersion?.build_configuration.embedding,
);

documents.setScope(projectId.value, showArchived.value);
documents.activate();
void documents.loadList(true);

watch(
  documentId,
  (id) => {
    file.value = null;
    if (fileInput.value) fileInput.value.value = "";
    if (id) activeTab.value = "documents";
    void documents.selectDocument(id);
  },
  { immediate: true },
);

onBeforeUnmount(() => documents.reset());

function choose(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null;
}

async function submit(retry = false) {
  if (rebuilding.value) return;
  const id = retry
    ? await documents.retryUpload()
    : file.value
      ? await documents.upload(file.value, documentId.value)
      : null;
  if (id) {
    file.value = null;
    if (fileInput.value) fileInput.value.value = "";
    if (documentId.value === id) await documents.selectDocument(id);
    else
      await router.push({
        path: `/documents/${id}`,
        query: projectQuery.value,
      });
  }
}

const date = (value: string) => new Date(value).toLocaleString("zh-CN");
</script>

<template>
  <section class="page-container document-page" aria-label="文档工作区">
    <header class="page-header">
      <div class="page-header-left">
        <div class="page-header-title-row">
          <h1>文档</h1>
          <span class="scope-badge" role="status">{{
            projectId
              ? `项目知识库 · ${workspace.projectName(projectId)}`
              : "个人知识库"
          }}</span>
        </div>
        <p class="page-header-sub">
          管理当前空间的知识库文档、构建状态与向量检索配置。
        </p>
      </div>

      <div class="page-header-actions">
        <RouterLink
          v-if="projectId"
          class="quiet-link"
          :to="`/projects/${projectId}`"
          >返回项目</RouterLink
        >
        <RouterLink v-if="projectId" class="quiet-link" to="/documents"
          >切换个人文档</RouterLink
        >
        <RouterLink
          class="quiet-link"
          :to="{
            path: '/documents',
            query: {
              ...(projectId ? { project: projectId } : {}),
              ...(showArchived ? {} : { archived: '1' }),
            },
          }"
          >{{ showArchived ? "查看可用文档" : "查看已下线文档" }}</RouterLink
        >
        <button
          class="secondary"
          :disabled="documents.loading"
          @click="documents.refresh"
        >
          刷新目录
        </button>
        <RouterLink
          class="btn-link primary"
          :to="{
            path: '/documents',
            query: projectId ? { project: projectId } : {},
          }"
          @click="activeTab = 'documents'"
          >上传新文档</RouterLink
        >
      </div>
    </header>

    <div class="workspace-tabs" role="tablist" aria-label="文档工作区分区">
      <button
        type="button"
        role="tab"
        class="workspace-tab"
        :class="{ 'is-active': activeTab === 'documents' }"
        :aria-selected="activeTab === 'documents'"
        @click="activeTab = 'documents'"
      >
        <span>文档管理</span>
        <span class="tab-badge">{{ documents.items.length }}</span>
      </button>
      <button
        type="button"
        role="tab"
        class="workspace-tab"
        :class="{ 'is-active': activeTab === 'models' }"
        :aria-selected="activeTab === 'models'"
        @click="activeTab = 'models'"
      >
        <span>检索模型设置</span>
        <span v-if="rebuilding" class="tab-badge">重建中</span>
      </button>
    </div>

    <!-- Tab 1: 文档管理 -->
    <div v-show="activeTab === 'documents'" class="documents-tab-panel">
      <div v-if="rebuilding" class="rebuild-banner" role="status">
        <span>当前知识库正在重建索引，重建期间暂停新文档上传。</span>
        <button
          type="button"
          class="quiet-button"
          @click="activeTab = 'models'"
        >
          查看重建进度 →
        </button>
      </div>

      <div class="page-split-grid">
        <aside class="panel-card-subtle document-sidebar" aria-label="我的文档">
          <div class="directory-heading">
            <h2>{{ showArchived ? "已下线文档" : "文档目录" }}</h2>
            <span class="muted">{{ documents.items.length }} 份</span>
          </div>
          <p v-if="documents.loading && !documents.items.length" role="status">
            正在读取文档…
          </p>
          <div v-if="documents.listError" class="document-error" role="alert">
            <p>{{ documents.listError }}</p>
            <button class="secondary" @click="documents.loadList(true)">
              重试加载
            </button>
          </div>
          <p
            v-else-if="!documents.loading && !documents.items.length"
            class="empty-directory muted"
          >
            {{
              showArchived
                ? "当前空间没有已下线文档。"
                : "还没有文档。在右侧上传第一份资料，开始整理你的知识库。"
            }}
          </p>
          <ul v-if="documents.items.length" class="document-list">
            <li v-for="item in documents.items" :key="item.id">
              <RouterLink
                :to="{ path: `/documents/${item.id}`, query: projectQuery }"
                :aria-current="documentId === item.id ? 'page' : undefined"
              >
                <strong>{{ item.title }}</strong>
                <span>
                  {{ item.current_version_id ? "已有发布版本" : "尚未发布" }} ·
                  {{ jobLabel(item.latest_job) }}
                </span>
              </RouterLink>
            </li>
          </ul>
          <button
            v-if="documents.nextOffset !== null"
            class="load-more"
            :disabled="documents.loading"
            @click="documents.loadList()"
          >
            {{ documents.loading ? "正在读取…" : "加载更多文档" }}
          </button>
        </aside>

        <div class="panel-card document-content">
          <p
            v-if="documents.detailLoading && !documents.selected"
            role="status"
          >
            正在读取版本与任务…
          </p>
          <div
            v-else-if="documents.detailError"
            class="document-error"
            role="alert"
          >
            <p>{{ documents.detailError }}</p>
            <button
              class="secondary"
              @click="documents.selectDocument(documentId)"
            >
              重试读取
            </button>
          </div>

          <template v-else-if="documents.selected">
            <header class="document-heading document-heading-row">
              <div>
                <h2>{{ documents.selected.title }}</h2>
                <p>每次更新保留独立版本，只有核验并发布成功的版本才可检索。</p>
              </div>
              <div class="document-lifecycle">
                <span
                  v-if="documents.selected.deleted_at"
                  class="archived-pill"
                  role="status"
                >
                  已下线
                </span>
                <button
                  v-else
                  class="secondary"
                  :disabled="documents.uploading"
                  @click="withdraw"
                >
                  下线文档
                </button>
              </div>
            </header>

            <p
              v-if="documents.selected.deleted_at"
              class="muted withdrawn-note"
              role="status"
            >
              文档已下线，不再用于新检索；历史版本和引用保留。
            </p>

            <div class="version-cards-grid">
              <section class="version-card" aria-labelledby="published-heading">
                <h3 id="published-heading">
                  {{
                    documents.selected.deleted_at ? "历史版本" : "当前可用版本"
                  }}
                </h3>
                <template
                  v-if="
                    documents.selected.current_version_id &&
                    !documents.selected.deleted_at
                  "
                >
                  <p class="published-state">
                    已发布<span v-if="documents.currentVersion">
                      · v{{ documents.currentVersion.version_number }}</span
                    >
                  </p>
                  <p v-if="documents.currentVersion" class="muted">
                    {{ documents.currentVersion.source_filename }} ·
                    {{ date(documents.currentVersion.created_at) }}
                  </p>
                  <p
                    v-if="
                      job?.status === 'failed' || job?.status === 'dead_letter'
                    "
                    class="muted"
                  >
                    新版未能发布，当前版本仍可检索。
                  </p>
                </template>
                <p v-else-if="documents.selected.deleted_at" class="muted">
                  已下线，历史版本保留，不再用于新检索。
                </p>
                <p v-else class="muted">
                  尚未发布。文件保存后，还需要完成索引构建与核验。
                </p>
              </section>

              <section
                class="version-card"
                aria-labelledby="job-heading"
                aria-live="polite"
              >
                <div class="job-heading">
                  <h3 id="job-heading">
                    最近构建<span v-if="documents.latestVersion">
                      · v{{ documents.latestVersion.version_number }}</span
                    >
                  </h3>
                  <span class="job-status-pill">{{ jobLabel(job) }}</span>
                </div>
                <p v-if="job?.status === 'pending'" class="muted">
                  文件已保存，等待索引处理。索引 Worker 需要在独立终端启动。
                </p>
                <p v-else-if="job?.status === 'staged'" class="muted">
                  索引已核验，等待发布事务确认。
                </p>
                <template
                  v-if="
                    activeJob(job) &&
                    job?.progress_total &&
                    job.progress_total > 0
                  "
                >
                  <progress
                    :value="job.progress_done"
                    :max="job.progress_total"
                    :aria-label="`${jobLabel(job)}进度`"
                  ></progress>
                  <p class="progress-count muted">
                    {{ job.progress_done }} /
                    {{ job.progress_total }} 个片段（当前阶段）
                  </p>
                </template>
                <p v-if="jobError(job)" class="document-error" role="alert">
                  {{ jobError(job) }}
                </p>
                <p v-if="profile?.id === 'bge-m3'" class="document-error">
                  此版本使用 BGE-M3，当前 Worker 尚不支持此构建配置。
                </p>
              </section>
            </div>

            <dl v-if="documents.latestVersion" class="document-metadata-grid">
              <div class="meta-cell">
                <dt>原文件</dt>
                <dd>{{ documents.latestVersion.source_filename }}</dd>
              </div>
              <div class="meta-cell">
                <dt>编码模型</dt>
                <dd>{{ profile?.id }} · {{ profile?.dimension }} 维</dd>
              </div>
              <div class="meta-cell">
                <dt>任务尝试</dt>
                <dd>{{ job?.attempt ?? 0 }}</dd>
              </div>
            </dl>
          </template>

          <header v-else class="document-heading">
            <h2>上传一份文档</h2>
            <p>将资料整理为可检索的版本。先从一份 Markdown 或 TXT 文件开始。</p>
          </header>

          <div
            v-if="documents.progressError"
            class="document-error progress-error"
            role="alert"
          >
            <p>{{ documents.progressError }}</p>
            <button class="secondary" @click="documents.refresh">
              重试更新进度
            </button>
          </div>

          <form
            v-if="
              !documentId ||
              (documents.selected && !documents.selected.deleted_at)
            "
            class="document-upload"
            @submit.prevent="submit()"
          >
            <h3>{{ documents.selected ? "上传新版" : "选择文档" }}</h3>
            <p v-if="documents.selected" class="muted">
              将新文件作为这份文档的新版本。构建期间，当前发布版本继续可用。
            </p>
            <label for="document-file">{{
              documents.selected ? "新版文件" : "文档文件"
            }}</label>
            <input
              id="document-file"
              ref="fileInput"
              type="file"
              accept=".md,.txt"
              :disabled="documents.uploading || rebuilding"
              aria-describedby="upload-help"
              @change="choose"
            />
            <p id="upload-help" class="upload-help">
              {{
                rebuilding
                  ? "索引重建期间暂停此知识库的上传。"
                  : "Markdown / TXT · UTF-8 · 最大 5 MiB"
              }}
            </p>
            <div class="upload-actions">
              <button
                class="primary"
                :disabled="!file || documents.uploading || rebuilding"
                type="submit"
              >
                {{
                  documents.uploading
                    ? "正在上传…"
                    : documents.selected
                      ? "提交新版构建"
                      : "上传并构建"
                }}
              </button>
              <span v-if="documents.uploading" role="status"
                >正在保存文件，请稍候。</span
              >
            </div>
            <div
              v-if="documents.uploadError"
              class="document-error"
              role="alert"
            >
              <p>{{ documents.uploadError }}</p>
              <button
                v-if="
                  documents.pendingUpload?.file === file &&
                  documents.pendingUpload?.target === documentId
                "
                type="button"
                class="secondary"
                :disabled="documents.uploading || rebuilding"
                @click="submit(true)"
              >
                重试本次上传
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- Tab 2: 检索模型设置（Embedding 与 Rerank 并排双栏） -->
    <div v-show="activeTab === 'models'" class="retrieval-models-grid">
      <div class="panel-card">
        <KnowledgeEmbedding
          :key="`${auth.user?.id}:${projectId}`"
          :project="projectId"
          @rebuild-state="rebuilding = $event"
        />
      </div>
      <div class="panel-card">
        <KnowledgeRerank
          :key="`rerank:${auth.user?.id}:${projectId}`"
          :project="projectId"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.rebuild-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px 16px;
  margin-bottom: 20px;
  border-radius: 10px;
  background: var(--bg-accent-softer);
  border: 1px solid var(--accent-border);
  color: var(--accent-text);
  font-size: 13px;
  font-weight: 500;
}

.directory-heading,
.job-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.directory-heading {
  margin-bottom: 12px;
}

.directory-heading h2 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: var(--ink);
}

.document-list {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 62vh;
  overflow-y: auto;
}

.document-list a {
  display: block;
  padding: 10px 12px;
  border-radius: 9px;
  margin-bottom: 4px;
  color: var(--ink-secondary);
  transition: background 0.12s;
}

.document-list a:hover {
  background: var(--bg-muted);
  color: var(--ink);
}

.document-list a[aria-current="page"] {
  background: var(--bg-accent-soft);
  color: var(--accent-text);
}

.document-list strong {
  display: block;
  font-size: 14px;
  overflow-wrap: anywhere;
  font-weight: 550;
}

.document-list span {
  display: block;
  font-size: 12px;
  color: var(--ink-muted);
  margin-top: 3px;
  line-height: 1.45;
}

.empty-directory {
  font-size: 13px;
}

.document-heading {
  margin-bottom: 20px;
}

.document-heading-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 14px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.document-heading h2 {
  font-size: 20px;
  letter-spacing: -0.015em;
  margin: 0 0 6px;
  overflow-wrap: anywhere;
}

.document-heading p {
  margin: 0;
  font-size: 14px;
  color: var(--ink-muted);
}

.archived-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--warn-bg);
  border: 1px solid var(--warn-border);
  color: var(--warn);
  font-size: 12px;
  font-weight: 600;
}

.withdrawn-note {
  margin: 0 0 16px;
}

.version-cards-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.version-card {
  padding: 16px 18px;
  border-radius: 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}

.version-card h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 8px;
}

.job-heading h3 {
  margin: 0;
}

.job-status-pill {
  font-size: 12px;
  font-weight: 550;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-accent-soft);
  color: var(--accent-text);
}

.published-state {
  color: var(--accent);
  font-weight: 600;
  margin-bottom: 6px;
}

.document-metadata-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 0 0 22px;
}

.meta-cell {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg-canvas);
}

.meta-cell dt {
  font-size: 12px;
  color: var(--ink-muted);
  margin-bottom: 4px;
}

.meta-cell dd {
  margin: 0;
  font-size: 13px;
  font-weight: 550;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.document-upload {
  padding-top: 20px;
  border-top: 1px solid var(--border);
  max-width: 680px;
}

.document-upload h3 {
  margin: 0 0 6px;
}

.document-upload label {
  display: block;
  font-size: 13px;
  margin: 14px 0 6px;
}

input[type="file"] {
  display: block;
  width: 100%;
  height: auto;
  max-width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--bg-canvas);
  caret-color: var(--accent);
}

input[type="file"]::file-selector-button {
  margin-right: 12px;
  border: 1px solid var(--accent-border);
  padding: 6px 12px;
  background: var(--bg-accent-softer);
  color: var(--accent-text);
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
}

.upload-help {
  color: var(--ink-muted);
  font-size: 12px;
}

.upload-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 14px 0 0;
}

.upload-actions span {
  color: var(--ink-muted);
  font-size: 13px;
}

.upload-actions .primary {
  margin-top: 0;
  width: auto;
  min-width: 140px;
}

.document-error,
.document-error p {
  color: var(--danger);
  font-size: 13px;
}

.progress-error {
  margin-bottom: 16px;
}

progress {
  display: block;
  width: 100%;
  height: 7px;
  accent-color: var(--accent);
  margin-top: 10px;
}

.progress-count {
  font-variant-numeric: tabular-nums;
  margin-top: 6px;
}

.retrieval-models-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
  align-items: start;
}

@media (max-width: 1024px) {
  .version-cards-grid,
  .retrieval-models-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .document-list {
    max-height: 220px;
  }
}
</style>
