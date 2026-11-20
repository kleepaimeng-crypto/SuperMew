<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const registering = computed(() => route.path === "/register");
const username = ref("");
const password = ref("");
const notice = ref("");
watch(registering, () => {
  password.value = "";
  auth.error = "";
});

async function submit() {
  notice.value = "";
  const credentials = {
    username: username.value.trim().toLowerCase(),
    password: password.value,
  };
  try {
    if (registering.value) {
      await auth.register(credentials);
      await router.push("/login");
      notice.value = "账户已创建，请使用刚才的密码登录。";
    } else {
      await auth.login(credentials);
      await router.replace("/threads");
    }
  } catch {
    /* Store 提供可公开的错误文案。 */
  } finally {
    password.value = "";
  }
}
</script>

<template>
  <section class="auth-layout">
    <div class="intro">
      <span class="eyebrow">MINIMEW / ACCOUNT</span>
      <h1>{{ registering ? "从这里开始。" : "欢迎回来。" }}</h1>
      <p>
        {{
          registering
            ? "创建一个账户，开启你的 MiniMew 空间。"
            : "登录你的账户，回到自己的空间。"
        }}
      </p>
      <div class="intro-mark" aria-hidden="true">m<span>·</span></div>
    </div>
    <div class="form-area">
      <nav class="auth-tabs" aria-label="账户操作">
        <RouterLink
          to="/login"
          :aria-current="!registering ? 'page' : undefined"
          >登录</RouterLink
        >
        <RouterLink
          to="/register"
          :aria-current="registering ? 'page' : undefined"
          >创建账户</RouterLink
        >
      </nav>
      <form @submit.prevent="submit">
        <h2>{{ registering ? "创建你的账户" : "登录 MiniMew" }}</h2>
        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <label for="username">用户名</label>
        <input
          id="username"
          v-model="username"
          autocomplete="username"
          required
          minlength="3"
          maxlength="32"
          pattern="[A-Za-z0-9_]{3,32}"
          placeholder="输入用户名"
          :disabled="auth.busy"
          aria-describedby="username-help"
        />
        <small id="username-help"
          >3–32 位字母、数字或下划线，不区分大小写。</small
        >
        <label for="password">密码</label>
        <input
          id="password"
          v-model="password"
          type="password"
          :autocomplete="registering ? 'new-password' : 'current-password'"
          required
          :minlength="registering ? 12 : 1"
          maxlength="128"
          placeholder="输入密码"
          :disabled="auth.busy"
        />
        <small v-if="registering">请使用 12–128 位密码。</small>
        <p v-if="auth.error" class="error" role="alert">{{ auth.error }}</p>
        <button class="primary" type="submit" :disabled="auth.busy">
          {{ auth.busy ? "请稍候…" : registering ? "创建账户" : "登录" }}
          <span aria-hidden="true">↗</span>
        </button>
      </form>
    </div>
  </section>
</template>
