<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import AccountModels from "../components/AccountModels.vue";
import RetrievalModels from "../components/RetrievalModels.vue";

const auth = useAuthStore();
const router = useRouter();
const activeTab = ref<"chat" | "retrieval">("chat");

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
  <section v-if="auth.user" class="page-container account-page">
    <header class="page-header">
      <div class="page-header-left">
        <span class="eyebrow">我的账户</span>
        <h1>你好，{{ auth.user.username }}。</h1>
        <p class="page-header-sub">
          管理你的账户信息、默认对话模型与项目可复用检索模型模板。
        </p>
      </div>
      <div class="page-header-actions">
        <RouterLink class="btn-link secondary" to="/threads">
          进入新会话 →
        </RouterLink>
        <button class="secondary" :disabled="auth.busy" @click="logout">
          {{ auth.busy ? "正在退出…" : "退出登录" }}
        </button>
      </div>
    </header>

    <section class="account-summary-strip" aria-label="基本信息">
      <div class="summary-avatar" aria-hidden="true">
        {{ (auth.user.username?.[0] ?? "U").toUpperCase() }}
      </div>
      <dl class="summary-dl">
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

    <div class="workspace-tabs" role="tablist" aria-label="账户设置分区">
      <button
        type="button"
        role="tab"
        class="workspace-tab"
        :class="{ 'is-active': activeTab === 'chat' }"
        :aria-selected="activeTab === 'chat'"
        @click="activeTab = 'chat'"
      >
        对话模型与知识偏好
      </button>
      <button
        type="button"
        role="tab"
        class="workspace-tab"
        :class="{ 'is-active': activeTab === 'retrieval' }"
        :aria-selected="activeTab === 'retrieval'"
        @click="activeTab = 'retrieval'"
      >
        检索模型模板
      </button>
    </div>

    <section v-if="activeTab === 'chat'" class="panel-card">
      <AccountModels />
    </section>

    <section v-else class="panel-card">
      <RetrievalModels :key="auth.user.id" />
    </section>
  </section>
</template>

<style scoped>
.account-summary-strip {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 16px 22px;
  margin-bottom: 24px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 14px;
}

.summary-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--accent);
  color: #ffffff;
  font-size: 18px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.summary-dl {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 14px 40px;
}

.summary-dl div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.summary-dl dt {
  font-size: 12px;
  color: var(--ink-muted);
}

.summary-dl dd {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.account-page :deep(.model-settings) {
  margin-top: 0;
  padding-top: 0;
  border-top: 0;
}
</style>
