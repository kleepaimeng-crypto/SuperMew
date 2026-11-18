<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useProjectsStore } from "../stores/projects";
import { useAuthStore } from "../stores/auth";
const route = useRoute(),
  router = useRouter(),
  projects = useProjectsStore(),
  auth = useAuthStore();
const name = ref(""),
  content = ref(""),
  drafts = ref<Record<string, { content: string; enabled: boolean }>>({});
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
onBeforeUnmount(() => projects.reset());
async function create() {
  const id = await projects.create(name.value);
  if (id) {
    name.value = "";
    await router.push(`/projects/${id}`);
  }
}
async function save() {
  if (await projects.save(content.value)) content.value = "";
}
</script>
<template>
  <section class="project-workspace" aria-label="项目工作区">
    <aside class="project-directory">
      <h1>项目</h1>
      <p>每个项目有自己的对话、文档和手工记忆。</p>
      <form @submit.prevent="create">
        <label for="project-name">新项目名称</label
        ><input
          id="project-name"
          v-model="name"
          maxlength="100"
          required
          :disabled="projects.busy"
        /><button class="primary" :disabled="projects.busy || !name.trim()">
          新建项目
        </button>
      </form>
      <nav aria-label="我的项目">
        <RouterLink
          v-for="project in projects.items"
          :key="project.id"
          :to="`/projects/${project.id}`"
          :aria-current="id === project.id ? 'page' : undefined"
          >{{ project.name }}</RouterLink
        >
      </nav>
      <p v-if="!projects.items.length && !projects.loading">还没有项目。</p>
      <button
        v-if="projects.nextOffset !== null"
        :disabled="projects.loading"
        @click="projects.load(true)"
      >
        更多项目</button
      ><button class="quiet-button" @click="projects.load()">刷新项目</button>
    </aside>
    <div class="project-content">
      <div v-if="projects.error" role="alert" class="project-error">
        <p>{{ projects.error }}</p>
        <button @click="id ? projects.select(id) : projects.load()">
          重新加载
        </button>
      </div>
      <template v-if="projects.selected">
        <h2>{{ projects.selected.name }}</h2>
        <div class="project-links">
          <RouterLink :to="{ path: '/threads', query: { project: id } }"
            >打开项目对话</RouterLink
          ><RouterLink :to="{ path: '/documents', query: { project: id } }"
            >管理项目文档</RouterLink
          >
        </div>
        <section class="project-memories" aria-label="项目记忆">
          <h3>项目记忆</h3>
          <p>
            由你明确保存，同项目的新对话可以读取。修改不影响已开始的执行；文档下线不会自动删除你保存的记忆。
          </p>
          <p class="project-note">
            最多启用20条，每条不超过500字符。每轮固定本次采用的内容与版本，不自动提取聊天。
          </p>
          <form @submit.prevent="save">
            <label for="memory-content">保存重要信息</label
            ><textarea
              id="memory-content"
              v-model="content"
              maxlength="500"
              rows="3"
              required
              :disabled="projects.busy"
              placeholder="例如：实验只使用2024年以前的数据。"
            ></textarea
            ><button
              class="primary"
              :disabled="projects.busy || !content.trim()"
            >
              保存项目记忆
            </button>
          </form>
          <p v-if="!projects.memories.length">暂未保存记忆。</p>
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
            <template v-if="drafts[memory.id]"
              ><label :for="`memory-${memory.id}`"
                >手工记忆 · 版本 {{ memory.revision }}</label
              ><textarea
                :id="`memory-${memory.id}`"
                v-model="drafts[memory.id]!.content"
                maxlength="500"
                rows="3"
                required
                :disabled="projects.busy"
              ></textarea
              ><label class="memory-toggle"
                ><input
                  v-model="drafts[memory.id]!.enabled"
                  type="checkbox"
                  :disabled="projects.busy"
                />用于后续对话</label
              >
              <div class="memory-actions">
                <button :disabled="projects.busy">保存修改</button
                ><button
                  type="button"
                  :disabled="projects.busy"
                  @click="confirmRemove(memory)"
                >
                  删除记忆
                </button>
              </div></template
            >
          </form>
        </section>
      </template>
      <template v-else-if="!id"
        ><h2>为一项工作建立独立空间</h2>
        <p>
          项目内的多段对话共用这个项目的资料和记忆。普通对话继续使用原来的个人知识库。
        </p>
        <RouterLink to="/threads">进入普通对话</RouterLink></template
      >
      <p v-else-if="!projects.error" role="status">正在读取项目…</p>
    </div>
  </section>
</template>
<style scoped>
.project-workspace {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 32px;
  width: 100%;
  min-height: 580px;
}
.project-directory {
  padding-right: 24px;
  border-right: 1px solid #dde2dc;
  min-width: 0;
}
h1 {
  font-size: 22px;
  letter-spacing: 0;
}
h2 {
  font-size: 28px;
  letter-spacing: 0;
  overflow-wrap: anywhere;
}
h3 {
  font-size: 20px;
}
.project-content {
  min-width: 0;
  max-width: 850px;
}
p {
  line-height: 1.7;
  max-width: 75ch;
}
label {
  display: block;
  margin: 12px 0 8px;
}
input,
textarea {
  font: inherit;
  max-width: 100%;
}
textarea {
  width: 100%;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #cbd4cb;
  border-radius: 6px;
  color: #243c2f;
  resize: vertical;
  min-height: 100px;
}
textarea:focus-visible {
  outline: 2px solid #3f6a50;
  outline-offset: 2px;
}
.project-directory nav {
  display: flex;
  flex-direction: column;
  margin: 24px 0;
  max-height: 55vh;
  overflow: auto;
}
.project-directory nav a {
  padding: 16px 12px;
  border-bottom: 1px solid #dde2dc;
  overflow-wrap: anywhere;
}
.project-directory nav a[aria-current="page"] {
  background: #e4ece4;
}
.project-links {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  margin: 24px 0;
}
.project-links a {
  color: #265b40;
  text-decoration: underline;
  text-underline-offset: 4px;
}
.project-memories {
  border-top: 1px solid #dde2dc;
  padding-top: 24px;
}
.project-note {
  font-size: 13px;
  color: #536259;
}
.memory-item {
  border-top: 1px solid #dde2dc;
  margin-top: 28px;
  padding-top: 20px;
}
.memory-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}
.memory-toggle input {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  padding: 0;
  margin: 0;
}
.memory-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.project-error {
  border: 1px solid #9a4c35;
  background: #fbf0e9;
  padding: 12px 16px;
}
@media (max-width: 800px) {
  .project-workspace {
    grid-template-columns: 1fr;
  }
  .project-directory {
    border-right: 0;
    border-bottom: 1px solid #dde2dc;
    padding: 0 0 24px;
  }
  .project-directory nav {
    max-height: 180px;
  }
  h2 {
    font-size: 25px;
  }
}
</style>
