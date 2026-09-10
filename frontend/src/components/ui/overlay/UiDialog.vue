<script setup lang="ts">
// 模态对话框组件（[前端详细设计 §6.1 M0 浮层底座]）
// 实现口径：v-model / size / title / closeOnMask / closeOnEsc / 焦点困于面板
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import { computed, nextTick, ref, watch } from 'vue'
import { useFocusTrap } from '../focus-trap'

type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    size?: Size
    title?: string
    closeOnMask?: boolean
    closeOnEsc?: boolean
    persistent?: boolean
    ariaLabel?: string
  }>(),
  {
    size: 'md',
    closeOnMask: true,
    closeOnEsc: true,
    persistent: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  (e: 'close'): void
}>()

// 面板 root ref（focus trap 目标）
const panelRef = ref<HTMLElement | null>(null)
const active = computed(() => props.modelValue)

useFocusTrap(panelRef, active)

// ESC 处理
function onKeydown(e: KeyboardEvent): void {
  if (!props.modelValue) return
  if (e.key === 'Escape' && props.closeOnEsc && !props.persistent) {
    e.stopPropagation()
    close()
  }
}

watch(active, (on) => {
  if (on) {
    nextTick(() => document.addEventListener('keydown', onKeydown))
  } else {
    document.removeEventListener('keydown', onKeydown)
  }
})

function close(): void {
  if (props.persistent) return
  emit('update:modelValue', false)
  emit('close')
}

function onMaskClick(): void {
  if (props.closeOnMask && !props.persistent) close()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="u-dialog">
      <div
        v-if="modelValue"
        class="u-dialog-mask"
        @mousedown.self="onMaskClick"
      >
        <div
          ref="panelRef"
          class="u-dialog"
          :class="[`u-dialog--${size}`]"
          role="dialog"
          aria-modal="true"
          :aria-label="ariaLabel ?? title"
          tabindex="-1"
        >
          <header
            v-if="title || $slots.header"
            class="u-dialog__header"
          >
            <slot name="header">
              <h3 class="u-dialog__title">
                {{ title }}
              </h3>
            </slot>
            <button
              v-if="!persistent"
              type="button"
              class="u-dialog__close"
              aria-label="关闭对话框"
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
          </header>
          <div class="u-dialog__body">
            <slot />
          </div>
          <footer
            v-if="$slots.footer"
            class="u-dialog__footer"
          >
            <slot name="footer" />
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.u-dialog-mask {
  position: fixed;
  inset: 0;
  z-index: 1500;
  background: rgba(15, 17, 23, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6);
}

.u-dialog {
  width: 100%;
  max-width: 560px;
  max-height: calc(100vh - var(--space-12));
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-modal);
  outline: none;
}

.u-dialog--sm {
  max-width: 400px;
}
.u-dialog--md {
  max-width: 560px;
}
.u-dialog--lg {
  max-width: 720px;
}

.u-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-6);
  border-bottom: 1px solid var(--color-border);
}

.u-dialog__title {
  margin: 0;
  font-size: var(--font-base);
  font-weight: 500;
  color: var(--color-text-strong);
}

.u-dialog__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.u-dialog__close:hover {
  background: var(--neutral-100);
  color: var(--color-text);
}

.u-dialog__body {
  flex: 1;
  min-height: 0;
  padding: var(--space-6);
  overflow: auto;
  color: var(--color-text);
  font-size: var(--font-sm);
}

.u-dialog__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border);
}

/* 进入/退出：仅 opacity + scale，避免重排 */
.u-dialog-enter-from,
.u-dialog-leave-to {
  opacity: 0;
}
.u-dialog-enter-from .u-dialog,
.u-dialog-leave-to .u-dialog {
  transform: scale(0.96);
}

.u-dialog-enter-active,
.u-dialog-leave-active {
  transition: opacity var(--motion-base) var(--ease-out);
}
.u-dialog-enter-active .u-dialog,
.u-dialog-leave-active .u-dialog {
  transition: transform var(--motion-base) var(--ease-out);
}
</style>
