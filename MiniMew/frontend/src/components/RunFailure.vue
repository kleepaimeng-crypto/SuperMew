<script setup lang="ts">
import type { FailureView } from "../runErrors";
defineProps<{ failure: FailureView | null; runId: string }>();
</script>

<template>
  <section
    v-if="failure"
    class="run-failure"
    role="status"
    aria-label="本次回答失败原因"
  >
    <p>{{ failure.reason }}</p>
    <p class="failure-action">{{ failure.action }}</p>
    <details>
      <summary>错误详情</summary>
      <p>
        错误码：<code>{{ failure.code }}</code>
      </p>
      <p>
        任务编号：<code>{{ runId }}</code>
      </p>
    </details>
  </section>
</template>

<style scoped>
.run-failure {
  margin-top: 10px;
  border: 1px solid var(--warn-border);
  border-left: 3px solid var(--warn);
  background: var(--warn-bg);
  border-radius: 8px;
  padding: 8px 12px;
  color: var(--warn);
  font-size: 13px;
  overflow-wrap: anywhere;
}
p {
  margin: 4px 0;
  color: inherit;
}
.failure-action,
details {
  font-size: 12px;
  line-height: 1.65;
}
summary {
  cursor: pointer;
  width: fit-content;
  padding: 4px 0;
}
</style>
