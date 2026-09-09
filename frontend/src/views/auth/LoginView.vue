<script setup lang="ts">
// 登录页（[前端详细设计 §4.2 M1]）：骨架
// M1 起接 `POST /auth/login`，当前里程碑仅占位以保证路由可跳转
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import UiButton from '@/components/ui/UiButton.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

// 临时入口：开发期跳过登录以验证受保护路由可达性
function devBypass() {
  session.setAccessToken('dev-mock-token')
  const redirect = (route.query.redirect as string) || '/home'
  router.replace(redirect)
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <h1>登录</h1>
      <p class="hint">M1 接入 /auth/login；当前为开发占位，可点击下方按钮跳过登录。</p>
      <UiButton variant="primary" size="lg" @click="devBypass">以开发模式进入</UiButton>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  padding: var(--space-8);
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-12) var(--space-8);
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  align-items: center;
}

.login-card h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  font-weight: 600;
}

.login-card .hint {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}
</style>
