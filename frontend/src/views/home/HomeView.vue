<script setup lang="ts">
// 首页（[前端详细设计 §11.1 M2 主体]）：当前里程碑仅占位
import UiCard from '@/components/ui/UiCard.vue'
</script>

<template>
  <section class="home-view">
    <UiCard size="lg">
      <h1 class="home-view__title">首页</h1>
      <p class="home-view__placeholder">
        意图路由单入口 + 进行中研究状态总览，将在 M2 里程碑实装。
      </p>
    </UiCard>
  </section>
</template>

<style scoped>
.home-view {
  flex: 1 1 auto;
  padding: var(--space-8);
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
}

.home-view__title {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  font-weight: 600;
  margin-bottom: var(--space-3);
}

.home-view__placeholder {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}
</style>
