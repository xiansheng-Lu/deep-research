<script setup lang="ts">
// 账户（[前端详细设计 §12.7 M1 骨架]）
// M1 仅展示当前用户契约字段与登出入口；团队管理等在后续里程碑
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import UiButton from '@/components/ui/UiButton.vue'
import { roleLabel } from '@/services/i18n/zh-CN'

const router = useRouter()
const session = useSessionStore()

// 进入受保护路由前守卫已完成 restore，user 必然存在
const user = computed(() => session.user!)

const loginTimeText = computed(() => {
  const raw = user.value.last_login_at
  if (!raw) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(new Date(raw))
})

const infoRows = computed(() => [
  { label: '姓名', value: user.value.display_name },
  { label: '邮箱', value: user.value.email },
  { label: '角色', value: roleLabel(user.value.role) },
  { label: '团队 ID', value: user.value.team_id },
  { label: '上次登录', value: loginTimeText.value }
])

async function onLogout(): Promise<void> {
  await session.logout()
  router.replace('/auth/login')
}
</script>

<template>
  <section class="account-view">
    <header class="account-view__head">
      <h1>账户</h1>
    </header>
    <div class="account-card">
      <dl class="account-card__list">
        <div
          v-for="row in infoRows"
          :key="row.label"
          class="account-card__row"
        >
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </div>
      </dl>
      <div class="account-card__footer">
        <UiButton
          variant="danger"
          @click="onLogout"
        >
          退出登录
        </UiButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.account-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
  padding: var(--space-8);
}

.account-view__head h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  margin-bottom: var(--space-6);
}

.account-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.account-card__list {
  margin: 0;
}

.account-card__row {
  display: flex;
  gap: var(--space-6);
  padding: var(--space-4) var(--space-6);
  border-bottom: 1px solid var(--color-border);
}

.account-card__row:last-child {
  border-bottom: none;
}

.account-card__row dt {
  width: 96px;
  flex-shrink: 0;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}

.account-card__row dd {
  font-size: var(--font-sm);
  color: var(--color-text);
  word-break: break-all;
}

.account-card__footer {
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border);
}
</style>
