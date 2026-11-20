<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import KnowledgeEmbedding from "../components/KnowledgeEmbedding.vue";
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
  rebuilding = ref(false);
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
async function logout() {
  try {
    await auth.logout();
    await router.replace("/login");
  } catch {
    /* 根视图提供重试。 */
  }
}
const date = (value: string) => new Date(value).toLocaleString("zh-CN");
</script>

<template>
  <section class="document-workspace" aria-label="文档工作区">
    <aside class="document-sidebar" aria-label="我的文档">
      <div class="doc-scope-banner" role="status">
        <span class="scope-badge">{{
          projectId
            ? `项目知识库 · ${workspace.projectName(projectId)}`
            : "个人知识库"
        }}</span>
        <div class="doc-scope-links">
          <RouterLink
            v-if="projectId"
            class="quiet-link"
            :to="`/projects/${projectId}`"
            >返回项目</RouterLink
          >
          <RouterLink v-if="projectId" class="quiet-link" to="/documents"
            >切换个人文档</RouterLink
          >
        </div>
      </div>
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
      <div class="directory-heading">
        <h1>文档</h1>
        <button
          class="quiet-button"
          :disabled="documents.loading"
          @click="documents.refresh"
        >
          刷新目录
        </button>
      </div>
      <RouterLink
        class="primary new-document"
        :to="{
          path: '/documents',
          query: projectId ? { project: projectId } : {},
        }"
        >上传新文档</RouterLink
      >
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
        class="empty-directory"
      >
        还没有文档。上传第一份资料，开始整理你的知识库。
      </p>
      <ul class="document-list">
        <li v-for="item in documents.items" :key="item.id">
          <RouterLink
            :to="{ path: `/documents/${item.id}`, query: projectQuery }"
            :aria-current="documentId === item.id ? 'page' : undefined"
          >
            <strong>{{ item.title }}</strong
            ><span
              >{{ item.current_version_id ? "已有发布版本" : "尚未发布" }} ·
              {{ jobLabel(item.latest_job) }}</span
            >
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
      <div class="document-account">
        <span>{{ auth.user?.username }}</span
        ><button class="quiet-button" :disabled="auth.busy" @click="logout">
          退出登录
        </button>
      </div>
    </aside>

    <div class="document-content">
      <KnowledgeEmbedding
        :key="`${auth.user?.id}:${projectId}`"
        :project="projectId"
        @rebuild-state="rebuilding = $event"
      />
      <p v-if="documents.detailLoading && !documents.selected" role="status">
        正在读取版本与任务…
      </p>
      <div
        v-else-if="documents.detailError"
        class="document-error"
        role="alert"
      >
        <p>{{ documents.detailError }}</p>
        <button class="secondary" @click="documents.selectDocument(documentId)">
          重试读取
        </button>
      </div>
      <template v-else-if="documents.selected">
        <header class="document-heading">
          <h2>{{ documents.selected.title }}</h2>
          <p>每次更新保留独立版本，只有核验并发布成功的版本才可检索。</p>
        </header>
        <section class="version-section" aria-labelledby="published-heading">
          <h3 id="published-heading">
            {{ documents.selected.deleted_at ? "历史版本" : "当前可用版本" }}
          </h3>
          <template
            v-if="
              documents.selected.current_version_id &&
              !documents.selected.deleted_at
            "
            ><p class="published-state">
              已发布<span v-if="documents.currentVersion">
                · v{{ documents.currentVersion.version_number }}</span
              >
            </p>
            <p v-if="documents.currentVersion">
              {{ documents.currentVersion.source_filename }} ·
              {{ date(documents.currentVersion.created_at) }}
            </p>
            <p v-if="job?.status === 'failed' || job?.status === 'dead_letter'">
              新版未能发布，当前版本仍可检索。
            </p></template
          >
          <p v-else-if="documents.selected.deleted_at">
            已下线，历史版本保留，不再用于新检索。
          </p>
          <p v-else>尚未发布。文件保存后，还需要完成索引构建与核验。</p>
        </section>
        <section
          class="version-section"
          aria-labelledby="job-heading"
          aria-live="polite"
        >
          <div class="job-heading">
            <h3 id="job-heading">
              最近构建<span v-if="documents.latestVersion">
                · v{{ documents.latestVersion.version_number }}</span
              >
            </h3>
            <span>{{ jobLabel(job) }}</span>
          </div>
          <p v-if="job?.status === 'pending'">
            文件已保存，等待索引处理。索引 Worker 需要在独立终端启动。
          </p>
          <p v-else-if="job?.status === 'staged'">
            索引已核验，等待发布事务确认。
          </p>
          <template
            v-if="
              activeJob(job) && job?.progress_total && job.progress_total > 0
            "
            ><progress
              :value="job.progress_done"
              :max="job.progress_total"
              :aria-label="`${jobLabel(job)}进度`"
            ></progress>
            <p class="progress-count">
              {{ job.progress_done }} /
              {{ job.progress_total }} 个片段（当前阶段）
            </p></template
          >
          <p v-if="jobError(job)" class="document-error" role="alert">
            {{ jobError(job) }}
          </p>
          <dl v-if="documents.latestVersion" class="document-metadata">
            <div>
              <dt>原文件</dt>
              <dd>{{ documents.latestVersion.source_filename }}</dd>
            </div>
            <div>
              <dt>编码模型</dt>
              <dd>{{ profile?.id }} · {{ profile?.dimension }} 维</dd>
            </div>
            <div>
              <dt>任务尝试</dt>
              <dd>{{ job?.attempt ?? 0 }}</dd>
            </div>
          </dl>
          <p v-if="profile?.id === 'bge-m3'" class="document-error">
            此版本使用 BGE-M3，当前 Worker 尚不支持此构建配置。
          </p>
        </section>
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
      <section v-if="documents.selected" class="document-lifecycle">
        <p v-if="documents.selected.deleted_at" role="status">
          文档已下线，不再用于新检索；历史版本和引用保留。
        </p>
        <button v-else :disabled="documents.uploading" @click="withdraw">
          下线文档
        </button>
      </section>
      <form
        v-if="
          !documentId || (documents.selected && !documents.selected.deleted_at)
        "
        class="document-upload"
        @submit.prevent="submit()"
      >
        <h3>{{ documents.selected ? "上传新版" : "选择文档" }}</h3>
        <p v-if="documents.selected">
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
            }}</button
          ><span v-if="documents.uploading" role="status"
            >正在保存文件，请稍候。</span
          >
        </div>
        <div v-if="documents.uploadError" class="document-error" role="alert">
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
  </section>
</template>

<style scoped>
.document-workspace {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  width: 100%;
  min-height: 560px;
}
.document-sidebar {
  display: flex;
  flex-direction: column;
  padding-right: 24px;
  border-right: 1px solid #dde2dc;
  min-width: 0;
}
.directory-heading,
.job-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.directory-heading h1 {
  font-size: 21px;
  letter-spacing: 0;
  margin: 12px 0 24px;
}
.new-document {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  padding: 12px 16px;
  border-radius: 6px;
  text-decoration: none;
}
.document-list {
  list-style: none;
  padding: 0;
  margin: 24px 0;
  flex: 1;
}
.document-list a {
  display: block;
  padding: 16px 12px;
  border-bottom: 1px solid #dde2dc;
  border-radius: 4px;
}
.document-list a:hover {
  background: #eef1ea;
}
.document-list a[aria-current="page"] {
  background: #e3ebe0;
  color: #234b35;
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
  color: #526356;
  margin-top: 8px;
  line-height: 1.6;
}
.empty-directory {
  font-size: 14px;
}
.document-account {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid #dde2dc;
  overflow-wrap: anywhere;
  font-size: 13px;
}
.document-content {
  min-width: 0;
  padding: 12px 0 32px 40px;
}
.document-heading h2 {
  font-size: 28px;
  letter-spacing: -0.02em;
  margin: 0 0 12px;
  overflow-wrap: anywhere;
}
.document-heading p,
.document-upload p,
.version-section p {
  max-width: 70ch;
  font-size: 14px;
}
.document-heading {
  margin-bottom: 32px;
}
.version-section {
  padding: 0 0 24px;
  margin-bottom: 24px;
  border-bottom: 1px solid #dde2dc;
}
h3 {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 16px;
}
.job-heading h3 {
  margin: 0;
}
.job-heading > span {
  font-size: 13px;
  color: #405b49;
}
.published-state {
  color: #234b35;
  font-weight: 600;
}
.document-metadata {
  margin: 16px 0 0;
  font-size: 13px;
}
.document-metadata div {
  padding: 10px 0;
  border: 0;
}
.document-metadata dt {
  width: 100px;
  flex-shrink: 0;
}
.document-metadata dd {
  min-width: 0;
}
.document-upload {
  margin-top: 32px;
  max-width: 72ch;
}
.document-upload label {
  display: block;
  font-size: 14px;
  margin: 20px 0 10px;
}
input[type="file"] {
  display: block;
  width: 100%;
  max-width: 100%;
  padding: 12px;
  border: 1px solid #bcc9bb;
  border-radius: 5px;
  background: #fff;
  caret-color: #294e39;
}
input[type="file"]::file-selector-button {
  margin-right: 12px;
  border: 0;
  padding: 8px 12px;
  background: #e3ebe0;
  color: #234b35;
  border-radius: 3px;
  cursor: pointer;
}
.upload-help {
  color: #526356;
  font-size: 12px;
}
.upload-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 20px 0;
}
.upload-actions span {
  color: #526356;
  font-size: 13px;
}
.upload-actions .primary {
  margin-top: 0;
  width: auto;
  min-width: 160px;
}
.document-error,
.document-error p {
  color: #92372e;
  font-size: 14px;
}
.progress-error {
  margin-bottom: 24px;
}
progress {
  display: block;
  width: 100%;
  height: 8px;
  accent-color: #42735a;
  margin-top: 20px;
}
.progress-count {
  font-variant-numeric: tabular-nums;
}
.document-content ::selection {
  background: #d6e5cf;
  color: #203b29;
}
@media (max-width: 760px) {
  .document-workspace {
    grid-template-columns: 1fr;
  }
  .document-sidebar {
    padding: 0 0 24px;
    border-right: 0;
    border-bottom: 1px solid #dde2dc;
  }
  .document-list {
    max-height: 220px;
    overflow: auto;
  }
  .document-content {
    padding: 28px 0;
  }
  .document-heading h2 {
    font-size: 24px;
  }
  .job-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }
}
</style>
