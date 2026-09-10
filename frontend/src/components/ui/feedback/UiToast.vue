<script setup lang="ts">
// 单条 Toast 组件（[前端详细设计 §6.1 M0 反馈]）
// 由 UiToastHost 渲染；自身负责自动关闭计时与进入动画
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { ToastItem } from '@/services/toast/toast'

const props = defineProps<{
  item: ToastItem
}>()

const emit = defineEmits<{
  (e: 'close', id: string): void
}>()

// 鼠标悬停暂停计时
const paused = ref(false)
let timer: number | null = null

function start(): void {
  if (props.item.duration <= 0) return
  timer = window.setTimeout(() => {
    emit('close', props.item.id)
  }, props.item.duration)
}

function stop(): void {
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
}

function onEnter(): void {
  paused.value = true
  stop()
}

function onLeave(): void {
  paused.value = false
  start()
}

function close(): void {
  emit('close', props.item.id)
}

onMounted(() => {
  start()
})

onBeforeUnmount(() => {
  stop()
})
</script>

<template>
  <div
    class="u-toast"
    :class="[`u-toast--${item.type}`]"
    role="status"
    aria-live="polite"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
  >
    <span
      class="u-toast__dot"
      aria-hidden="true"
    />
    <div class="u-toast__body">
      <div class="u-toast__msg">
        {{ item.message }}
      </div>
      <div
        v-if="item.description"
        class="u-toast__desc"
      >
        {{ item.description }}
      </div>
    </div>
    <button
      v-if="item.closable"
      type="button"
      class="u-toast__close"
      aria-label="关闭通知"
      @click="close"
    >
      <svg
        viewBox="0 0 16 16"
        width="14"
        height="14"
        aria-hidden="true"
      >
        <path
          d="M3 3 L13 13 M13 3 L3 13"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
        />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.u-toast {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  min-width: 280px;
  max-width: 420px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left-width: 3px;
  border-radius: var(--radius-md);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
  color: var(--color-text);
  font-size: var(--font-sm);
  pointer-events: auto;
}

.u-toast__dot {
  width: 8px;
  height: 8px;
  margin-top: 6px;
  border-radius: var(--radius-full);
  background: currentColor;
  flex-shrink: 0;
}

.u-toast__body {
  flex: 1;
  min-width: 0;
}

.u-toast__msg {
  font-weight: 500;
  color: var(--color-text-strong);
  word-break: break-word;
}

.u-toast__desc {
  margin-top: var(--space-1);
  color: var(--color-text-muted);
  word-break: break-word;
}

.u-toast__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  margin-left: var(--space-2);
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  flex-shrink: 0;
}

.u-toast__close:hover {
  background: var(--neutral-100);
  color: var(--color-text);
}

/* 变体：左侧色条 + dot 颜色 */
.u-toast--info {
  border-left-color: var(--info-500);
  color: var(--info-500);
}
.u-toast--success {
  border-left-color: var(--success-500);
  color: var(--success-500);
}
.u-toast--warn {
  border-left-color: var(--warning-500);
  color: var(--warning-500);
}
.u-toast--danger {
  border-left-color: var(--danger-500);
  color: var(--danger-500);
}
</style>
