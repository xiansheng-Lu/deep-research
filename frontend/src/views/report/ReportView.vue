<script setup lang="ts">
// 报告阅读页（[前端详细设计 §11.4 M1 最简/M2 结构化/M4 协作]）：占位
import { useRoute } from 'vue-router'
const route = useRoute()
const projectId = String(route.params.projectId)
const runId = String(route.params.runId)
</script>

<template>
  <section class="placeholder-view">
    <h1>报告</h1>
    <p>研究结果阅读页。Run：{{ runId }}（项目 {{ projectId }}）。</p>
  </section>
</template>

<style scoped>
.placeholder-view {
  flex: 1 1 auto;
  padding: var(--space-8);
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
}

.placeholder-view h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  margin-bottom: var(--space-3);
}

.placeholder-view p {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}
</style>
