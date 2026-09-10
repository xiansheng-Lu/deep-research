<script setup lang="ts">
// 登录页（[前端详细设计 §4.2 / openapi-m1 POST /auth/login]）
// 邮箱+密码表单，提交后由 session store 存令牌对并拉取当前用户
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import UiButton from '@/components/ui/UiButton.vue'
import UiInput from '@/components/ui/form/UiInput.vue'
import type { ApiError } from '@/services/http/error'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const email = ref('')
const password = ref('')
const submitting = ref(false)
const formError = ref('')
const emailError = ref('')

// 回跳地址仅接受站内相对路径（以单个 / 开头），拒绝协议相对地址与外链
const redirectTarget = computed(() => {
  const raw = route.query.redirect
  const target = typeof raw === 'string' ? raw : ''
  if (target.startsWith('/') && !target.startsWith('//')) return target
  return '/projects'
})

function validate(): boolean {
  emailError.value = ''
  if (!email.value.trim()) {
    emailError.value = '请输入邮箱'
    return false
  }
  if (!password.value) {
    formError.value = '请输入密码'
    return false
  }
  return true
}

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  formError.value = ''
  if (!validate()) return
  submitting.value = true
  try {
    await session.login(email.value.trim(), password.value)
    router.replace(redirectTarget.value)
  } catch (err) {
    const apiError = err as ApiError
    formError.value = apiError.detail || '登录失败，请检查邮箱与密码'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-view">
    <form
      class="login-card"
      novalidate
      @submit.prevent="onSubmit"
    >
      <h1>登录 AI 研究者助手</h1>
      <UiInput
        v-model="email"
        type="email"
        label="邮箱"
        placeholder="name@example.com"
        autocomplete="username"
        :error="emailError"
        @enter="onSubmit"
      />
      <UiInput
        v-model="password"
        type="password"
        label="密码"
        placeholder="请输入密码"
        autocomplete="current-password"
        @enter="onSubmit"
      />
      <p
        v-if="formError"
        class="login-error"
        role="alert"
      >
        {{ formError }}
      </p>
      <UiButton
        variant="primary"
        size="lg"
        native-type="submit"
        :loading="submitting"
        class="login-submit"
      >
        登录
      </UiButton>
    </form>
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
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.login-card h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  font-weight: 600;
  text-align: center;
  margin-bottom: var(--space-2);
}

.login-error {
  color: var(--danger-500);
  font-size: var(--font-sm);
}

.login-submit {
  width: 100%;
  margin-top: var(--space-2);
}
</style>
