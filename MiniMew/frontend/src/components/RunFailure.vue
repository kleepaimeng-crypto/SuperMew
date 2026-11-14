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
  margin-top: 12px;
  border-left: 3px solid #997345;
  padding: 2px 12px;
  color: #76542f;
  font-size: 13px;
  overflow-wrap: anywhere;
}
p {
  margin: 6px 0;
}
.failure-action,
details {
  font-size: 12px;
  line-height: 1.7;
}
summary {
  cursor: pointer;
  width: fit-content;
  padding: 5px 0;
}
</style>
