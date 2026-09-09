<script setup lang="ts">
// 项目内任务列表（[前端详细设计 §12.2 M1]）：占位
import { useRoute } from 'vue-router'
const route = useRoute()
const projectId = String(route.params.projectId)
</script>

<template>
  <section class="placeholder-view">
    <h1>任务列表</h1>
    <p>项目内研究任务列表，当前项目 ID：{{ projectId }}。</p>
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
