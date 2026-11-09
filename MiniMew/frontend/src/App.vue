<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";
import { watch } from "vue";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
watch(
  () => auth.status,
  (status) => {
    if (status === "anonymous" && route.meta.requiresAuth)
      void router.replace("/login");
  },
);
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
    await router.replace(route.meta.requiresAuth ? route.fullPath : "/account");
  if (auth.status === "anonymous") await router.replace("/login");
}
</script>

<template>
  <div
    class="shell"
    :class="{
      'workspace-shell':
        route.path.startsWith('/threads') && auth.status === 'authenticated',
    }"
  >
    <header class="masthead">
      <RouterLink class="brand" to="/"
        >MiniMew<span class="brand-dot">.</span></RouterLink
      >
      <nav
        v-if="auth.status === 'authenticated'"
        class="workspace-nav"
        aria-label="主导航"
      >
        <RouterLink to="/threads">对话</RouterLink>
        <RouterLink to="/account">账户</RouterLink>
      </nav>
      <span v-else class="header-label">我的空间</span>
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
