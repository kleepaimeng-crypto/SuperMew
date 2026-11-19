import { it, expect, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";
import { useChatStore } from "./chat";
import { ids, time } from "../../tests/fixtures/chat";

it("未知提交与刷新恢复保留原Skill，换对话清理选择，旧目录迟到不泄漏", async () => {
  setActivePinia(createPinia());
  const saved = new Map<string, string>(),
    posted: unknown[] = [];
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (k: string, v: string) => saved.set(k, v),
    removeItem: (k: string) => saved.delete(k),
  });
  const json = (v: unknown, status = 200) =>
    new Response(JSON.stringify(v), { status });
  let finish!: (v: Response) => void;
  let delayed = false;
  const skill = {
    id: "document-qa",
    version: "1.0.0",
    name: "文档问答",
    description: "依据资料回答",
    digest: "a".repeat(64),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/v1/skills")
        return delayed
          ? new Promise<Response>((r) => {
              finish = r;
            })
          : json([skill]);
      if (init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return json({}, 503);
      }
      return json({ items: [], previous_cursor: null });
    }),
  );
  const auth = useAuthStore();
  auth.user = { id: "user-a", username: "alice", created_at: time };
  auth.status = "authenticated";
  auth.accessToken = "token";
  const chat = useChatStore();
  try {
    await chat.open(ids.thread);
    await chat.loadSkills();
    chat.selectedSkill = "document-qa@1.0.0";
    chat.draft = "资料问题";
    expect(await chat.submit()).toBe(false);
    const intent = { ...chat.pending };
    expect(intent.skill).toEqual({ id: "document-qa", version: "1.0.0" });
    await chat.open(ids.thread);
    expect(chat.selectedSkill).toBe("document-qa@1.0.0");
    expect(chat.pending).toEqual(intent);
    chat.selectedSkill = "";
    expect(await chat.submit()).toBe(false);
    expect(posted[1]).toEqual(posted[0]);
    delayed = true;
    const old = chat.loadSkills();
    await chat.open(ids.user);
    finish(json([skill]));
    await old;
    expect(chat.skills).toEqual([]);
    expect(chat.selectedSkill).toBe("");
  } finally {
    chat.close();
    vi.unstubAllGlobals();
  }
});
