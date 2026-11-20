<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import WorkspaceSidebar from "./components/WorkspaceSidebar.vue";
import { useAuthStore } from "./stores/auth";
import { useWorkspaceStore } from "./stores/workspace";

const auth = useAuthStore();
const workspace = useWorkspaceStore();
const router = useRouter();
const route = useRoute();

const isAuthenticatedWorkspace = computed(
  () => auth.status === "authenticated",
);

const mobileHeading = computed(() => {
  if (route.path.startsWith("/documents")) {
    return workspace.activeScope
      ? `文档 · ${workspace.projectName(workspace.activeScope)}`
      : "文档 · 个人";
  }
  if (route.path.startsWith("/evaluations")) return "评测";
  if (route.path.startsWith("/projects")) return "项目";
  if (route.path.startsWith("/account")) return "账户";
  return route.params.threadId ? "对话" : "新会话";
});

watch(
  () => auth.status,
  (status) => {
    if (status === "anonymous" && route.meta.requiresAuth)
      void router.replace("/login");
  },
);

watch(
  [
    () => auth.status,
    () => route.path,
    () => route.params.projectId,
    () => route.query.project,
  ],
  ([status, path, paramProject, queryProject]) => {
    if (status !== "authenticated") return;
    workspace.closeMobileNav();
    if (path.startsWith("/projects") && typeof paramProject === "string") {
      workspace.setActiveScope(paramProject, false);
      return;
    }
    if (path.startsWith("/threads") || path.startsWith("/documents")) {
      const next = typeof queryProject === "string" ? queryProject : null;
      workspace.setActiveScope(next, path.startsWith("/threads"));
    }
  },
  { immediate: true },
);

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && workspace.mobileNavOpen) {
    workspace.closeMobileNav();
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));

async function retry() {
  if (auth.logoutPending) {
    try {
      await auth.logout();
    } catch {
      return;
    }
  } else {
    await auth.restore();
  }
  if (auth.status === "authenticated")
    await router.replace(route.meta.requiresAuth ? route.fullPath : "/threads");
  if (auth.status === "anonymous") await router.replace("/login");
}
</script>

<template>
  <div v-if="isAuthenticatedWorkspace" class="app-workspace-shell">
    <header class="ws-mobile-topbar">
      <button
        type="button"
        class="quiet-button ws-mobile-menu-btn"
        :aria-expanded="workspace.mobileNavOpen"
        aria-label="打开导航栏"
        @click="workspace.openMobileNav()"
      >
        菜单
      </button>
      <span class="ws-mobile-title">{{ mobileHeading }}</span>
      <RouterLink
        class="quiet-button ws-mobile-new-btn"
        :to="
          workspace.activeScope
            ? { path: '/threads', query: { project: workspace.activeScope } }
            : '/threads'
        "
      >
        新会话
      </RouterLink>
    </header>

    <div
      v-if="workspace.mobileNavOpen"
      class="ws-backdrop"
      aria-hidden="true"
      @click="workspace.closeMobileNav()"
    />

    <WorkspaceSidebar />

    <main class="ws-main">
      <RouterView />
    </main>
  </div>

  <div v-else class="shell">
    <header class="masthead">
      <RouterLink class="brand" to="/"
        >MiniMew<span class="brand-dot">.</span></RouterLink
      >
      <span class="header-label">我的空间</span>
    </header>
    <main>
      <section
        v-if="auth.status === 'idle' || auth.status === 'restoring'"
        class="state-page"
        aria-live="polite"
      >
        <span class="spinner" aria-hidden="true"></span>
        <h1>正在恢复登录</h1>
        <p>请稍候，我们正在确认你的账户状态。</p>
      </section>
      <section
        v-else-if="auth.status === 'error'"
        class="state-page"
        aria-live="polite"
      >
        <span class="eyebrow">暂时无法连接</span>
        <h1>
          {{ auth.logoutPending ? "退出尚未完成" : "连接遇到了一点问题" }}
        </h1>
        <p role="alert">{{ auth.error }}</p>
        <button class="primary" :disabled="auth.busy" @click="retry">
          {{ auth.logoutPending ? "重试退出" : "重新尝试" }}
        </button>
      </section>
      <RouterView v-else />
    </main>
    <footer>MiniMew <span>我的空间</span></footer>
  </div>
</template>
