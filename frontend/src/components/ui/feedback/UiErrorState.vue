<script setup lang="ts">
// 错误态组件（[前端详细设计 §6.1 M0]）
// 实现口径：error / onRetry / onBack（错误+重试）
// WP-17：autoRetry 开启后按 1s/2s/4s 退避自动重试 3 次，期间禁用重试按钮并展示
// 倒计时；3 次仍失败转手动重试。页面在重试请求开始时会先把 error 置空，
// 用 requestInFlight 区分“请求中清空”与“恢复后清空”，保证计数不被重置。
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ApiError } from '@/services/http/error'
import { zhCN } from '@/services/i18n/zh-CN'

const props = withDefaults(
  defineProps<{
    error?: ApiError | null
    title?: string
    showRetry?: boolean
    showBack?: boolean
    autoRetry?: boolean
  }>(),
  {
    error: null,
    showRetry: true,
    showBack: false,
    autoRetry: false
  }
)

const emit = defineEmits<{
  (e: 'retry'): void
  (e: 'back'): void
}>()

// 退避序列与次数上限为联调韧性固定口径（WP-17）
const RETRY_DELAYS_MS = [1000, 2000, 4000]
const MAX_AUTO_ATTEMPTS = 3

// 已发起的自动重试次数；非 0 等待时长用于倒计时文案
const attempts = ref(0)
const nextDelayMs = ref(0)
// 是否处于自动等待中（控制提示行与按钮禁用）
const waiting = ref(false)
// 重试请求已发出、等待页面回填结果
let requestInFlight = false
let timer: number | undefined

function clearTimer(): void {
  if (timer !== undefined) {
    window.clearTimeout(timer)
    timer = undefined
  }
  waiting.value = false
}

// 调度下一次自动重试；达到上限后停留手动重试态
function scheduleNext(): void {
  if (!props.autoRetry || props.showRetry === false) return
  if (attempts.value >= MAX_AUTO_ATTEMPTS) return
  nextDelayMs.value = RETRY_DELAYS_MS[Math.min(attempts.value, RETRY_DELAYS_MS.length - 1)]
  waiting.value = true
  timer = window.setTimeout(() => {
    timer = undefined
    waiting.value = false
    attempts.value += 1
    requestInFlight = true
    emit('retry')
  }, nextDelayMs.value)
}

watch(
  () => props.error,
  (value) => {
    clearTimer()
    if (!value) {
      // 请求进行中的清空保留计数；真正恢复（无在途请求）时复位序列
      if (requestInFlight) return
      attempts.value = 0
      return
    }
    if (requestInFlight) {
      // 重试请求落地为新错误：消费在途标记，沿用既有计数继续退避
      requestInFlight = false
    } else {
      // 错误首次出现：开启新一轮自动重试序列
      attempts.value = 0
    }
    scheduleNext()
  },
  { immediate: true }
)

// 手动点击重试：非自动重试模式仅透传事件；自动模式下用户介入后重新计数，
// 再给一轮自动重试机会
function onRetryClick(): void {
  if (!props.autoRetry) {
    emit('retry')
    return
  }
  clearTimer()
  attempts.value = 0
  requestInFlight = true
  emit('retry')
}

// 自动次数用尽且当前不在等待中：转手动重试提示
const autoRetryExhausted = computed(
  () => props.autoRetry && attempts.value >= MAX_AUTO_ATTEMPTS && !waiting.value
)

onBeforeUnmount(clearTimer)
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
    <details
      v-if="error?.code || error?.traceId"
      class="u-error__meta"
    >
      <summary>技术详情</summary>
      <span v-if="error?.code">{{ error.code }}</span>
      <span v-if="error?.traceId">· trace: {{ error.traceId }}</span>
    </details>
    <p
      v-if="autoRetry && waiting"
      class="u-error__autoretry"
      aria-live="polite"
    >
      {{ nextDelayMs / 1000 }}{{ zhCN.errors.autoRetryHint }}（第 {{ attempts + 1 }}/{{ MAX_AUTO_ATTEMPTS }} 次）
    </p>
    <p
      v-else-if="autoRetryExhausted"
      class="u-error__autoretry"
    >
      {{ zhCN.errors.autoRetryGiveUp }}
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
        :disabled="waiting"
        @click="onRetryClick"
      >
        {{ waiting ? '自动重试中…' : '重试' }}
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

.u-error__meta summary {
  cursor: pointer;
  font-family: inherit;
}

.u-error__autoretry {
  margin: var(--space-3) 0 0;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
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

.u-error__btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>