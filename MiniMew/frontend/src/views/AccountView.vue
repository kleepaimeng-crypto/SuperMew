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
    /* 根视图提供重试。 */
  }
}
</script>

<template>
  <section v-if="auth.user" class="account-page">
    <span class="eyebrow">我的账户</span>
    <h1>你好，{{ auth.user.username }}。</h1>
    <p>你已登录 MiniMew。</p>
    <RouterLink class="text-link" to="/threads">进入我的对话 →</RouterLink>
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
    <AccountModels />
    <button class="secondary" :disabled="auth.busy" @click="logout">
      {{ auth.busy ? "正在退出…" : "退出登录" }}
    </button>
  </section>
</template>
