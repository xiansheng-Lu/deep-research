<script setup lang="ts">
// 右侧抽屉组件（[前端详细设计 §6.1 M0 浮层底座]）
// 实现口径：v-model / size / title / closeOnMask / closeOnEsc / 焦点困于面板 / 右侧滑入
// 复用 RightDrawer 抽象：与 UiDrawer 同源组件，业务模块按需 import 命名别名
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

const panelRef = ref<HTMLElement | null>(null)
const active = computed(() => props.modelValue)
useFocusTrap(panelRef, active)

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
    <Transition name="u-drawer">
      <div
        v-if="modelValue"
        class="u-drawer-mask"
        @mousedown.self="onMaskClick"
      >
        <aside
          ref="panelRef"
          class="u-drawer"
          :class="[`u-drawer--${size}`]"
          role="dialog"
          aria-modal="true"
          :aria-label="ariaLabel ?? title"
          tabindex="-1"
        >
          <header
            v-if="title || $slots.header"
            class="u-drawer__header"
          >
            <slot name="header">
              <h3 class="u-drawer__title">
                {{ title }}
              </h3>
            </slot>
            <button
              v-if="!persistent"
              type="button"
              class="u-drawer__close"
              aria-label="关闭抽屉"
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
          <div class="u-drawer__body">
            <slot />
          </div>
          <footer
            v-if="$slots.footer"
            class="u-drawer__footer"
          >
            <slot name="footer" />
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.u-drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 1500;
  background: rgba(15, 17, 23, 0.4);
  display: flex;
  justify-content: flex-end;
}

.u-drawer {
  position: relative;
  width: 480px;
  max-width: 90vw;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  box-shadow: var(--shadow-drawer);
  outline: none;
}

.u-drawer--sm {
  width: 320px;
}
.u-drawer--md {
  width: 480px;
}
.u-drawer--lg {
  width: 640px;
}

.u-drawer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-6);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.u-drawer__title {
  margin: 0;
  font-size: var(--font-base);
  font-weight: 500;
  color: var(--color-text-strong);
}

.u-drawer__close {
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

.u-drawer__close:hover {
  background: var(--neutral-100);
  color: var(--color-text);
}

.u-drawer__body {
  flex: 1;
  min-height: 0;
  padding: var(--space-6);
  overflow: auto;
  color: var(--color-text);
  font-size: var(--font-sm);
}

.u-drawer__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

/* 进入/退出：mask 淡入，抽屉从右侧滑入 */
.u-drawer-enter-from,
.u-drawer-leave-to {
  opacity: 0;
}
.u-drawer-enter-from .u-drawer,
.u-drawer-leave-to .u-drawer {
  transform: translateX(100%);
}

.u-drawer-enter-active,
.u-drawer-leave-active {
  transition: opacity var(--motion-base) var(--ease-out);
}
.u-drawer-enter-active .u-drawer,
.u-drawer-leave-active .u-drawer {
  transition: transform var(--motion-base) var(--ease-out);
}
</style>
