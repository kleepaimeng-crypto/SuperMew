<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { useProjectsStore } from "../stores/projects";
import {
  PERSONAL_SCOPE,
  scopeKeyFor,
  useWorkspaceStore,
} from "../stores/workspace";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const projects = useProjectsStore();
const workspace = useWorkspaceStore();

const currentThreadId = computed(() =>
  typeof route.params.threadId === "string"
    ? route.params.threadId.toLowerCase()
    : "",
);

const newChatTarget = computed(() =>
  workspace.activeScope
    ? { path: "/threads", query: { project: workspace.activeScope } }
    : { path: "/threads" },
);

const documentsTarget = computed(() =>
  workspace.activeScope
    ? { path: "/documents", query: { project: workspace.activeScope } }
    : { path: "/documents" },
);

const activeScopeLabel = computed(() =>
  workspace.activeScope ? workspace.projectName(workspace.activeScope) : "个人",
);

const personalBucket = computed(() => workspace.ensureBucket(PERSONAL_SCOPE));

onMounted(() => {
  if (auth.status === "authenticated") {
    if (
      workspace.expandedScopes[PERSONAL_SCOPE] &&
      !personalBucket.value.loaded &&
      !personalBucket.value.loading
    ) {
      void workspace.loadScopeThreads(null, true);
    }
    workspace.syncProjectsIfNeeded();
  }
});

function formatTime(iso: string) {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function selectPersonal() {
  workspace.toggleScope(null);
}

function selectProject(projectId: string) {
  workspace.toggleScope(projectId);
}

function navigateAndClose() {
  workspace.closeMobileNav();
}

async function logout() {
  workspace.closeMobileNav();
  try {
    await auth.logout();
    await router.replace("/login");
  } catch {
    /* 根视图提供退出重试。 */
  }
}
</script>

<template>
  <aside
    class="ws-sidebar"
    :class="{ 'is-open': workspace.mobileNavOpen }"
    aria-label="工作区导航"
  >
    <div class="ws-sidebar-header">
      <RouterLink
        class="brand ws-brand"
        to="/threads"
        @click="navigateAndClose"
      >
        MiniMew<span class="brand-dot">.</span>
      </RouterLink>
      <button
        type="button"
        class="quiet-button ws-mobile-close"
        aria-label="关闭导航栏"
        @click="workspace.closeMobileNav()"
      >
        关闭
      </button>
    </div>

    <div class="ws-primary-action">
      <RouterLink
        class="primary ws-new-chat-btn"
        :to="newChatTarget"
        @click="navigateAndClose"
      >
        <span>＋ 新会话</span>
        <span class="ws-new-chat-scope">{{ activeScopeLabel }}</span>
      </RouterLink>
    </div>

    <nav class="ws-top-links" aria-label="一级导航">
      <RouterLink
        class="ws-nav-item"
        :class="{ 'is-active': route.path.startsWith('/documents') }"
        :to="documentsTarget"
        @click="navigateAndClose"
      >
        <span class="ws-nav-item-main">
          <svg
            class="ws-nav-icon"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            aria-hidden="true"
          >
            <path
              d="M5 3.5h6.5L15.5 7.5v9a1 1 0 0 1-1 1h-9.5a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1Z"
            />
            <path d="M11.5 3.5v4h4M7.5 11h5M7.5 14h3.5" />
          </svg>
          <span>文档</span>
        </span>
        <span class="ws-scope-pill" :title="`当前范围：${activeScopeLabel}`">{{
          activeScopeLabel
        }}</span>
      </RouterLink>
      <RouterLink
        class="ws-nav-item"
        :class="{ 'is-active': route.path.startsWith('/evaluations') }"
        to="/evaluations"
        @click="navigateAndClose"
      >
        <span class="ws-nav-item-main">
          <svg
            class="ws-nav-icon"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            aria-hidden="true"
          >
            <path d="M4 15.5h12M6.5 15.5V10M10 15.5V6.5M13.5 15.5V8.5" />
          </svg>
          <span>评测</span>
        </span>
        <span class="ws-scope-pill ws-scope-global">全局</span>
      </RouterLink>
    </nav>

    <div class="ws-scroll-area">
      <!-- 个人空间 -->
      <section class="ws-section" aria-label="个人空间会话">
        <div class="ws-section-header">
          <button
            type="button"
            class="ws-tree-toggle"
            :class="{ 'is-active-scope': workspace.activeScope === null }"
            :aria-expanded="!!workspace.expandedScopes[PERSONAL_SCOPE]"
            @click="selectPersonal"
          >
            <span class="ws-chevron" aria-hidden="true">{{
              workspace.expandedScopes[PERSONAL_SCOPE] ? "▾" : "▸"
            }}</span>
            <span class="ws-tree-title">个人空间</span>
          </button>
          <button
            type="button"
            class="quiet-button ws-icon-btn"
            :disabled="personalBucket.loading"
            aria-label="刷新个人会话列表"
            @click="workspace.loadScopeThreads(null, true)"
          >
            刷新
          </button>
        </div>

        <div
          v-if="workspace.expandedScopes[PERSONAL_SCOPE]"
          class="ws-scope-body"
        >
          <p
            v-if="personalBucket.loading && !personalBucket.loaded"
            class="ws-muted"
            role="status"
          >
            正在加载对话…
          </p>
          <p
            v-else-if="
              personalBucket.loaded &&
              !personalBucket.items.length &&
              !personalBucket.error
            "
            class="ws-muted"
          >
            还没有个人会话。
          </p>
          <ul v-if="personalBucket.items.length" class="ws-thread-list">
            <li v-for="thread in personalBucket.items" :key="thread.id">
              <RouterLink
                class="ws-thread-link"
                :to="`/threads/${thread.id}`"
                :aria-current="
                  currentThreadId === thread.id.toLowerCase() &&
                  !route.query.project
                    ? 'page'
                    : undefined
                "
                @click="
                  workspace.setActiveScope(null, true);
                  navigateAndClose();
                "
              >
                <span class="ws-thread-title">{{
                  thread.title || "未命名对话"
                }}</span>
                <time :datetime="thread.created_at">{{
                  formatTime(thread.created_at)
                }}</time>
              </RouterLink>
            </li>
          </ul>
          <div v-if="personalBucket.error" class="ws-inline-error">
            <p class="error" role="alert">{{ personalBucket.error }}</p>
            <button
              type="button"
              class="quiet-button"
              @click="workspace.loadScopeThreads(null, !personalBucket.loaded)"
            >
              重试
            </button>
          </div>
          <button
            v-if="personalBucket.nextOffset !== null"
            type="button"
            class="load-more ws-load-more"
            :disabled="personalBucket.loading"
            @click="workspace.loadScopeThreads(null, false)"
          >
            {{ personalBucket.loading ? "正在加载…" : "加载更多" }}
          </button>
        </div>
      </section>

      <!-- 项目列表与各自会话 -->
      <section class="ws-section" aria-label="项目列表">
        <div class="ws-section-header">
          <span class="ws-section-label">项目</span>
          <div class="ws-section-actions">
            <button
              type="button"
              class="quiet-button ws-icon-btn"
              :disabled="projects.loading"
              aria-label="刷新项目列表"
              @click="projects.load(false)"
            >
              刷新
            </button>
            <RouterLink
              class="quiet-button ws-icon-btn"
              to="/projects"
              @click="navigateAndClose"
            >
              管理
            </RouterLink>
          </div>
        </div>

        <p
          v-if="projects.loading && !projects.items.length"
          class="ws-muted"
          role="status"
        >
          正在加载项目…
        </p>
        <p
          v-else-if="!projects.items.length && !projects.error"
          class="ws-muted"
        >
          还没有项目，可前往<RouterLink
            class="text-link"
            to="/projects"
            @click="navigateAndClose"
            >新建项目</RouterLink
          >。
        </p>

        <ul v-if="projects.items.length" class="ws-project-list">
          <li
            v-for="project in projects.items"
            :key="project.id"
            class="ws-project-item"
          >
            <div class="ws-project-row">
              <button
                type="button"
                class="ws-tree-toggle ws-project-toggle"
                :class="{
                  'is-active-scope': workspace.activeScope === project.id,
                }"
                :aria-expanded="
                  !!workspace.expandedScopes[scopeKeyFor(project.id)]
                "
                @click="selectProject(project.id)"
              >
                <span class="ws-chevron" aria-hidden="true">{{
                  workspace.expandedScopes[scopeKeyFor(project.id)] ? "▾" : "▸"
                }}</span>
                <span class="ws-tree-title">{{ project.name }}</span>
              </button>
              <RouterLink
                class="quiet-button ws-project-overview-link"
                :to="`/projects/${project.id}`"
                :aria-label="`${project.name} 概览与设置`"
                :aria-current="
                  route.path === `/projects/${project.id}` ? 'page' : undefined
                "
                @click="
                  workspace.setActiveScope(project.id, false);
                  navigateAndClose();
                "
              >
                概览
              </RouterLink>
            </div>

            <div
              v-if="workspace.expandedScopes[scopeKeyFor(project.id)]"
              class="ws-scope-body ws-project-threads"
            >
              <div class="ws-project-subbar">
                <RouterLink
                  class="ws-sub-action"
                  :to="{ path: '/threads', query: { project: project.id } }"
                  @click="
                    workspace.setActiveScope(project.id, true);
                    navigateAndClose();
                  "
                >
                  ＋ 本项目新会话
                </RouterLink>
                <button
                  type="button"
                  class="quiet-button ws-icon-btn"
                  :disabled="
                    workspace.ensureBucket(scopeKeyFor(project.id)).loading
                  "
                  :aria-label="`刷新 ${project.name} 会话`"
                  @click="workspace.loadScopeThreads(project.id, true)"
                >
                  刷新
                </button>
              </div>

              <p
                v-if="
                  workspace.ensureBucket(scopeKeyFor(project.id)).loading &&
                  !workspace.ensureBucket(scopeKeyFor(project.id)).loaded
                "
                class="ws-muted"
                role="status"
              >
                正在加载对话…
              </p>
              <p
                v-else-if="
                  workspace.ensureBucket(scopeKeyFor(project.id)).loaded &&
                  !workspace.ensureBucket(scopeKeyFor(project.id)).items
                    .length &&
                  !workspace.ensureBucket(scopeKeyFor(project.id)).error
                "
                class="ws-muted"
              >
                该项目还没有会话。
              </p>

              <ul
                v-if="
                  workspace.ensureBucket(scopeKeyFor(project.id)).items.length
                "
                class="ws-thread-list"
              >
                <li
                  v-for="thread in workspace.ensureBucket(
                    scopeKeyFor(project.id),
                  ).items"
                  :key="thread.id"
                >
                  <RouterLink
                    class="ws-thread-link"
                    :to="{
                      path: `/threads/${thread.id}`,
                      query: { project: project.id },
                    }"
                    :aria-current="
                      currentThreadId === thread.id.toLowerCase() &&
                      route.query.project === project.id
                        ? 'page'
                        : undefined
                    "
                    @click="
                      workspace.setActiveScope(project.id, true);
                      navigateAndClose();
                    "
                  >
                    <span class="ws-thread-title">{{
                      thread.title || "未命名对话"
                    }}</span>
                    <time :datetime="thread.created_at">{{
                      formatTime(thread.created_at)
                    }}</time>
                  </RouterLink>
                </li>
              </ul>

              <div
                v-if="workspace.ensureBucket(scopeKeyFor(project.id)).error"
                class="ws-inline-error"
              >
                <p class="error" role="alert">
                  {{ workspace.ensureBucket(scopeKeyFor(project.id)).error }}
                </p>
                <button
                  type="button"
                  class="quiet-button"
                  @click="
                    workspace.loadScopeThreads(
                      project.id,
                      !workspace.ensureBucket(scopeKeyFor(project.id)).loaded,
                    )
                  "
                >
                  重试
                </button>
              </div>

              <button
                v-if="
                  workspace.ensureBucket(scopeKeyFor(project.id)).nextOffset !==
                  null
                "
                type="button"
                class="load-more ws-load-more"
                :disabled="
                  workspace.ensureBucket(scopeKeyFor(project.id)).loading
                "
                @click="workspace.loadScopeThreads(project.id, false)"
              >
                {{
                  workspace.ensureBucket(scopeKeyFor(project.id)).loading
                    ? "正在加载…"
                    : "加载更多"
                }}
              </button>
            </div>
          </li>
        </ul>

        <button
          v-if="projects.nextOffset !== null"
          type="button"
          class="load-more ws-load-more"
          :disabled="projects.loading"
          @click="projects.load(true)"
        >
          {{ projects.loading ? "正在加载…" : "更多项目" }}
        </button>
      </section>
    </div>

    <div class="ws-account-footer">
      <RouterLink
        class="ws-account-link"
        :class="{ 'is-active': route.path === '/account' }"
        to="/account"
        @click="navigateAndClose"
      >
        <span class="ws-avatar" aria-hidden="true">{{
          (auth.user?.username?.[0] ?? "U").toUpperCase()
        }}</span>
        <span class="ws-account-text">
          <span class="ws-account-name">{{ auth.user?.username }}</span>
          <span class="ws-account-sub">账户与模型</span>
        </span>
      </RouterLink>
      <button
        type="button"
        class="quiet-button ws-logout-btn"
        :disabled="auth.busy"
        @click="logout"
      >
        退出登录
      </button>
    </div>
  </aside>
</template>
