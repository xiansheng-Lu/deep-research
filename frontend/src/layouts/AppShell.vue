<script setup lang="ts">
// 布局壳：顶栏 + 内容区（[前端详细设计 §4.1]）
// 仅包裹受保护路由；登录/错误页等公共路由不走此壳
// M2 起呈现首页（意图单入口）与助手（闲聊）导航
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import UiButton from '@/components/ui/UiButton.vue'
import UiDropdown, { type DropdownItem } from '@/components/ui/overlay/UiDropdown.vue'

const router = useRouter()
const session = useSessionStore()

const userMenuItems: DropdownItem[] = [
  { key: 'account', label: '账户设置' },
  { key: 'logout', label: '退出登录', danger: true }
]

const loggingOut = ref(false)

async function onUserMenu(key: string): Promise<void> {
  if (key === 'account') {
    router.push('/account')
    return
  }
  if (key === 'logout') {
    if (loggingOut.value) return
    loggingOut.value = true
    await session.logout()
    router.replace('/auth/login')
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="app-shell__topbar">
      <RouterLink
        to="/home"
        class="app-shell__logo"
      >
        AI 研究者助手
      </RouterLink>
      <nav
        class="app-shell__nav"
        aria-label="主导航"
      >
        <RouterLink
          to="/home"
          class="app-shell__nav-link"
        >
          首页
        </RouterLink>
        <RouterLink
          to="/assistant"
          class="app-shell__nav-link"
        >
          助手
        </RouterLink>
        <RouterLink
          to="/projects"
          class="app-shell__nav-link"
        >
          项目
        </RouterLink>
        <RouterLink
          to="/account"
          class="app-shell__nav-link"
        >
          账户
        </RouterLink>
      </nav>
      <div class="app-shell__actions">
        <UiButton
          variant="primary"
          size="sm"
          @click="router.push('/wizard')"
        >
          + 发起研究
        </UiButton>
        <UiDropdown
          :items="userMenuItems"
          aria-label="账户菜单"
          @select="onUserMenu"
        >
          <template #trigger>
            <span class="app-shell__user">{{ session.user?.display_name ?? '账户' }}</span>
          </template>
        </UiDropdown>
      </div>
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
  gap: var(--space-6);
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
  white-space: nowrap;
}

.app-shell__nav {
  display: flex;
  gap: var(--space-2);
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

.app-shell__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.app-shell__user {
  font-size: var(--font-sm);
  color: var(--color-text);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-shell__main {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
}
</style>
