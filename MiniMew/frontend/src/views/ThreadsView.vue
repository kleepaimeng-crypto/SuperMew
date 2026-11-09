<script setup lang="ts">
import { computed, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useThreadsStore } from "../stores/threads";
import { useAuthStore } from "../stores/auth";
import ChatPanel from "../components/ChatPanel.vue";

const route = useRoute();
const router = useRouter();
const threads = useThreadsStore();
const auth = useAuthStore();
const title = computed(
  () =>
    threads.items.find((thread) => thread.id === threads.selected?.id)?.title ??
    threads.selected?.title,
);
const id = computed(() =>
  typeof route.params.threadId === "string" ? route.params.threadId : "",
);
watch(
  id,
  (value) =>
    value ? void threads.selectThread(value) : threads.clearSelection(),
  { immediate: true },
);
if (!threads.loaded) void threads.loadList(true);
onUnmounted(() => threads.clearSelection());

async function create() {
  const threadId = await threads.createThread();
  if (threadId) await router.push(`/threads/${threadId}`);
}
async function logout() {
  try {
    await auth.logout();
    await router.replace("/login");
  } catch {
    /* 根视图提供退出重试。 */
  }
}
</script>

<template>
  <section class="thread-workspace">
    <aside class="thread-sidebar" aria-label="对话列表">
      <div class="sidebar-heading">
        <h1>我的对话</h1>
        <button
          class="quiet-button"
          :disabled="threads.listLoading"
          @click="threads.loadList(true)"
        >
          刷新列表
        </button>
      </div>
      <button
        class="primary new-thread"
        :disabled="threads.creating"
        @click="create"
      >
        {{ threads.creating ? "正在创建…" : "＋ 新建对话" }}
      </button>
      <p v-if="threads.createError" class="error" role="alert">
        {{ threads.createError }}
      </p>
      <div class="thread-list-area">
        <p v-if="threads.listLoading && !threads.loaded" role="status">
          正在加载对话…
        </p>
        <p
          v-if="threads.loaded && !threads.items.length && !threads.listLoading"
          class="muted"
        >
          还没有对话，创建第一段吧。
        </p>
        <ul class="thread-list">
          <li v-for="thread in threads.items" :key="thread.id">
            <RouterLink
              :to="`/threads/${thread.id}`"
              :aria-current="
                id.toLowerCase() === thread.id.toLowerCase()
                  ? 'page'
                  : undefined
              "
            >
              <span class="thread-title">{{
                thread.title || "未命名对话"
              }}</span>
              <time :datetime="thread.created_at">{{
                new Date(thread.created_at).toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })
              }}</time>
            </RouterLink>
          </li>
        </ul>
        <div v-if="threads.listError" class="list-error">
          <p class="error" role="alert">{{ threads.listError }}</p>
          <button class="secondary" @click="threads.loadList(!threads.loaded)">
            重试加载列表
          </button>
        </div>
        <button
          v-else-if="threads.nextOffset !== null"
          class="load-more"
          :disabled="threads.listLoading"
          @click="threads.loadList()"
        >
          {{ threads.listLoading ? "正在加载…" : "加载更多" }}
        </button>
      </div>
      <div class="sidebar-account">
        <span>{{ auth.user?.username }}</span
        ><button class="quiet-button" :disabled="auth.busy" @click="logout">
          退出登录
        </button>
      </div>
    </aside>
    <section class="thread-content" aria-label="当前对话">
      <div v-if="threads.detailLoading" class="thread-empty">
        <span class="spinner" aria-hidden="true"></span>
        <h2>正在打开对话</h2>
      </div>
      <div v-else-if="threads.unavailable" class="thread-empty">
        <span class="eyebrow">无法打开</span>
        <h2>对话不存在或不可访问</h2>
        <p>请从左侧选择其他对话，或新建一段对话。</p>
        <RouterLink class="text-link" to="/threads">返回对话列表 →</RouterLink>
      </div>
      <div v-else-if="threads.detailError" class="thread-empty">
        <h2>暂时无法打开对话</h2>
        <p class="error" role="alert">{{ threads.detailError }}</p>
        <button class="secondary" @click="threads.selectThread(id)">
          重试打开对话
        </button>
      </div>
      <template v-else-if="threads.selected">
        <header class="thread-heading">
          <h2>{{ title || "未命名对话" }}</h2>
        </header>
        <ChatPanel
          :key="threads.selected.id"
          :thread-id="threads.selected.id"
        />
      </template>
      <div v-else class="thread-empty">
        <span class="empty-symbol" aria-hidden="true">◇</span>
        <h2>选择一段对话</h2>
        <p>从左侧打开已有对话，或创建一段新对话。</p>
      </div>
    </section>
  </section>
</template>
