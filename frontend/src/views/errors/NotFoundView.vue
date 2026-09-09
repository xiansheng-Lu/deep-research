<script setup lang="ts">
// 404 视图
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="not-found-view">
    <h1>404</h1>
    <p>资源不存在或无权访问。</p>
    <button class="link" @click="router.replace('/home')">返回首页</button>
  </div>
</template>

<style scoped>
.not-found-view {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  background: var(--color-bg);
}

.not-found-view h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-2xl);
}

.not-found-view p {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}

.not-found-view .link {
  border: none;
  background: none;
  color: var(--brand-500);
  cursor: pointer;
  font-size: var(--font-sm);
  padding: var(--space-2) var(--space-3);
}
.not-found-view .link:hover {
  text-decoration: underline;
}
</style>
