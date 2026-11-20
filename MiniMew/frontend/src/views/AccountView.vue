<script setup lang="ts">
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import AccountModels from "../components/AccountModels.vue";

const auth = useAuthStore();
const router = useRouter();

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
  <section v-if="auth.user" class="account-page">
    <header class="page-header">
      <div class="page-header-left">
        <span class="eyebrow">我的账户</span>
        <h1>你好，{{ auth.user.username }}。</h1>
        <p class="page-header-sub">
          管理你的账户信息、默认对话模型与知识检索设置。
        </p>
      </div>
      <div class="page-header-actions">
        <RouterLink class="secondary account-nav-btn" to="/threads"
          >进入新会话 →</RouterLink
        >
        <button class="secondary" :disabled="auth.busy" @click="logout">
          {{ auth.busy ? "正在退出…" : "退出登录" }}
        </button>
      </div>
    </header>

    <div class="account-card-stack">
      <section class="panel-card" aria-label="基本信息">
        <dl>
          <div>
            <dt>用户名</dt>
            <dd>{{ auth.user.username }}</dd>
          </div>
          <div>
            <dt>注册日期</dt>
            <dd>
              {{ new Date(auth.user.created_at).toLocaleDateString("zh-CN") }}
            </dd>
          </div>
        </dl>
      </section>

      <section class="panel-card">
        <AccountModels />
      </section>
    </div>
  </section>
</template>

<style scoped>
.account-nav-btn {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid var(--border-strong);
  font-size: 13px;
  font-weight: 500;
}
.account-page :deep(.model-settings) {
  margin-top: 0;
  padding-top: 0;
  border-top: 0;
}
</style>
