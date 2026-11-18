import type { Pinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "./stores/auth";
import AuthView from "./views/AuthView.vue";
import AccountView from "./views/AccountView.vue";
import ThreadsView from "./views/ThreadsView.vue";
import DocumentsView from "./views/DocumentsView.vue";
import ProjectsView from "./views/ProjectsView.vue";
import EvaluationsView from "./views/EvaluationsView.vue";

export function makeRouter(pinia: Pinia) {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      {
        path: "/projects/:projectId?",
        component: ProjectsView,
        meta: { requiresAuth: true },
      },
      {
        path: "/evaluations/:jobId?",
        component: EvaluationsView,
        meta: { requiresAuth: true },
      },
      { path: "/", redirect: "/account" },
      { path: "/login", component: AuthView },
      { path: "/register", component: AuthView },
      {
        path: "/threads/:threadId?",
        component: ThreadsView,
        meta: { requiresAuth: true },
      },
      {
        path: "/documents/:documentId?",
        component: DocumentsView,
        meta: { requiresAuth: true },
      },
      {
        path: "/account",
        component: AccountView,
        meta: { requiresAuth: true },
      },
      { path: "/:pathMatch(.*)*", redirect: "/account" },
    ],
  });
  router.beforeEach(async (to) => {
    const auth = useAuthStore(pinia);
    await auth.ensureReady();
    if (auth.status === "anonymous" && to.meta.requiresAuth) return "/login";
    if (
      auth.status === "authenticated" &&
      ["/login", "/register"].includes(to.path)
    )
      return "/account";
    // error 交由根视图呈现重试，不当作 anonymous 跳转登录。
  });
  return router;
}
