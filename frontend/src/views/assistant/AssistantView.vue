<script setup lang="ts">
// 助手面板（[前端详细设计 §12.6 M2]）：占位
</script>

<template>
  <section class="placeholder-view">
    <h1>助手</h1>
    <p>独立闲聊会话区，将在 M2 里程碑实装。</p>
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
