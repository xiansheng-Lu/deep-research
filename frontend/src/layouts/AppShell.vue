<script setup lang="ts">
// 布局壳：顶栏 + 内容区（[前端详细设计 §4.1]）
// 仅包裹受保护路由；登录/错误页等公共路由不走此壳
// 完整 TopBar/侧栏/RightDrawer 在 M1 里程碑补齐，本骨架先呈现"已登录壳"结构
</script>

<template>
  <div class="app-shell">
    <header class="app-shell__topbar">
      <RouterLink to="/home" class="app-shell__logo">AI 研究者助手</RouterLink>
      <nav class="app-shell__nav" aria-label="主导航">
        <RouterLink to="/home" class="app-shell__nav-link">首页</RouterLink>
        <RouterLink to="/projects" class="app-shell__nav-link">项目</RouterLink>
        <RouterLink to="/assistant" class="app-shell__nav-link">助手</RouterLink>
        <RouterLink to="/account" class="app-shell__nav-link">账户</RouterLink>
      </nav>
    </header>
    <main class="app-shell__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--color-bg);
  color: var(--color-text);
}

.app-shell__topbar {
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 var(--space-8);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  z-index: 100;
}

.app-shell__logo {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-md);
  font-weight: 600;
  color: var(--color-text-strong);
  letter-spacing: -0.01em;
}

.app-shell__nav {
  display: flex;
  gap: var(--space-2);
  margin-left: var(--space-12);
}

.app-shell__nav-link {
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--neutral-600);
  border-radius: var(--radius-sm);
  transition: background var(--motion-base) var(--ease-out),
    color var(--motion-base) var(--ease-out);
}

.app-shell__nav-link:hover {
  background: var(--neutral-100);
  color: var(--color-text-strong);
}

.app-shell__nav-link.router-link-active {
  color: var(--brand-700);
  background: var(--brand-50);
}

.app-shell__main {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
}
</style>
