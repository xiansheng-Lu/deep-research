<script setup lang="ts">
// 错误态组件（[前端详细设计 §6.1 M0]）
// 实现口径：error / onRetry / onBack（错误+重试）
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import type { ApiError } from '@/services/http/error'

withDefaults(
  defineProps<{
    error?: ApiError | null
    title?: string
    showRetry?: boolean
    showBack?: boolean
  }>(),
  {
    error: null,
    showRetry: true,
    showBack: false
  }
)

const emit = defineEmits<{
  (e: 'retry'): void
  (e: 'back'): void
}>()
</script>

<template>
  <div
    class="u-error"
    role="alert"
  >
    <div
      class="u-error__icon"
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 48 48"
        class="u-error__svg"
      >
        <circle
          cx="24"
          cy="24"
          r="20"
          stroke="currentColor"
          stroke-width="2"
          fill="none"
        />
        <path
          d="M24 14 V26 M24 32 V33"
          stroke="currentColor"
          stroke-width="2.5"
          stroke-linecap="round"
        />
      </svg>
    </div>
    <h3 class="u-error__title">
      {{ title || error?.title || '出现错误' }}
    </h3>
    <p
      v-if="error?.detail"
      class="u-error__detail"
    >
      {{ error.detail }}
    </p>
    <p
      v-else-if="$slots.default"
      class="u-error__detail"
    >
      <slot />
    </p>
    <p
      v-if="error?.code || error?.traceId"
      class="u-error__meta"
    >
      <span v-if="error?.code">{{ error.code }}</span>
      <span v-if="error?.traceId">· trace: {{ error.traceId }}</span>
    </p>
    <div class="u-error__actions">
      <button
        v-if="showBack"
        type="button"
        class="u-error__btn"
        @click="emit('back')"
      >
        返回
      </button>
      <button
        v-if="showRetry"
        type="button"
        class="u-error__btn u-error__btn--primary"
        @click="emit('retry')"
      >
        重试
      </button>
    </div>
  </div>
</template>

<style scoped>
.u-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: var(--space-12) var(--space-6);
  color: var(--color-text-muted);
}

.u-error__icon {
  width: 48px;
  height: 48px;
  color: var(--danger-500);
  margin-bottom: var(--space-4);
}

.u-error__svg {
  width: 100%;
  height: 100%;
}

.u-error__title {
  margin: 0;
  font-size: var(--font-base);
  font-weight: 500;
  color: var(--color-text-strong);
}

.u-error__detail {
  margin: var(--space-2) 0 0;
  font-size: var(--font-sm);
  max-width: 420px;
}

.u-error__meta {
  margin: var(--space-2) 0 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-family: ui-monospace, monospace;
}

.u-error__actions {
  margin-top: var(--space-6);
  display: flex;
  gap: var(--space-3);
}

.u-error__btn {
  height: 32px;
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  border-radius: var(--radius-sm);
  font-size: var(--font-sm);
  cursor: pointer;
}

.u-error__btn--primary {
  background: var(--brand-500);
  color: var(--neutral-0);
  border-color: var(--brand-500);
}
</style>