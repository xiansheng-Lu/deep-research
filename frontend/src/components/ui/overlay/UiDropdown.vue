<script setup lang="ts">
// 下拉菜单组件（[前端详细设计 §6.1 M0 浮层]）
// 实现口径：items: {key,label,disabled,danger}[] / 键盘上下+Enter
// 基于 @floating-ui/dom 定位
// 样式仅引用 tokens.css 的 CSS 变量
import {
  autoUpdate,
  computePosition,
  flip,
  offset as offsetMiddleware,
  shift,
  type Placement
} from '@floating-ui/dom'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

export interface DropdownItem {
  key: string
  label: string
  disabled?: boolean
  danger?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue?: string
    items: DropdownItem[]
    placeholder?: string
    placement?: Placement
    ariaLabel?: string
  }>(),
  {
    placement: 'bottom-start',
    placeholder: '请选择'
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', key: string): void
  (e: 'select', key: string): void
}>()

const triggerRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const open = ref(false)
const focusIdx = ref<number>(-1)

const current = computed(() => props.items.find((i) => i.key === props.modelValue))

let cleanupAuto: (() => void) | null = null

async function updatePosition(): Promise<void> {
  const t = triggerRef.value
  const m = menuRef.value
  if (!t || !m) return
  const { x, y } = await computePosition(t, m, {
    placement: props.placement,
    middleware: [offsetMiddleware(4), flip(), shift({ padding: 8 })]
  })
  Object.assign(m.style, { left: `${x}px`, top: `${y}px` })
}

watch(open, async (on) => {
  if (on) {
    await nextTick()
    if (!triggerRef.value || !menuRef.value) return
    cleanupAuto = autoUpdate(triggerRef.value, menuRef.value, updatePosition)
    await updatePosition()
    // 默认聚焦到当前选中
    const cur = props.items.findIndex((i) => i.key === props.modelValue)
    focusIdx.value = cur >= 0 ? cur : (props.items.findIndex((i) => !i.disabled) ?? 0)
    requestAnimationFrame(() => focusItem(focusIdx.value))
    document.addEventListener('mousedown', onDocMouseDown)
    document.addEventListener('keydown', onKeydown)
  } else {
    cleanupAuto?.()
    cleanupAuto = null
    document.removeEventListener('mousedown', onDocMouseDown)
    document.removeEventListener('keydown', onKeydown)
  }
})

function focusItem(idx: number): void {
  const root = menuRef.value
  if (!root) return
  const items = Array.from(root.querySelectorAll<HTMLElement>('.u-dropdown__item:not(.is-disabled)'))
  const target = items[idx]
  target?.focus()
}

function onDocMouseDown(e: MouseEvent): void {
  const t = e.target as Node | null
  if (!t) return
  if (menuRef.value?.contains(t)) return
  if (triggerRef.value?.contains(t)) return
  close()
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.stopPropagation()
    close()
    triggerRef.value?.focus()
    return
  }
  const enabledIdxs = props.items
    .map((it, i) => (it.disabled ? -1 : i))
    .filter((i) => i >= 0)
  if (enabledIdxs.length === 0) return

  let next: number | null = null
  if (e.key === 'ArrowDown') {
    const cur = enabledIdxs.indexOf(focusIdx.value)
    next = enabledIdxs[(cur + 1) % enabledIdxs.length]
  } else if (e.key === 'ArrowUp') {
    const cur = enabledIdxs.indexOf(focusIdx.value)
    next = enabledIdxs[(cur - 1 + enabledIdxs.length) % enabledIdxs.length]
  } else if (e.key === 'Home') {
    next = enabledIdxs[0]
  } else if (e.key === 'End') {
    next = enabledIdxs[enabledIdxs.length - 1]
  } else if (e.key === 'Enter' || e.key === ' ') {
    if (focusIdx.value >= 0) {
      e.preventDefault()
      const it = props.items[focusIdx.value]
      if (it && !it.disabled) select(it.key)
    }
    return
  }
  if (next !== null && next !== undefined) {
    e.preventDefault()
    focusIdx.value = next
    focusItem(next)
  }
}

function toggle(): void {
  open.value ? close() : (open.value = true)
}

function close(): void {
  open.value = false
}

function select(key: string): void {
  emit('update:modelValue', key)
  emit('select', key)
  close()
  triggerRef.value?.focus()
}

onBeforeUnmount(() => {
  cleanupAuto?.()
  document.removeEventListener('mousedown', onDocMouseDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <button
    ref="triggerRef"
    type="button"
    class="u-dropdown-trigger"
    :aria-haspopup="'listbox'"
    :aria-expanded="open"
    :aria-label="ariaLabel"
    @click="toggle"
  >
    <span class="u-dropdown-trigger__label">
      <slot name="trigger">{{ current?.label ?? placeholder }}</slot>
    </span>
    <svg
      class="u-dropdown-trigger__caret"
      :class="{ 'is-open': open }"
      viewBox="0 0 12 12"
      width="12"
      height="12"
      aria-hidden="true"
    >
      <path d="M2 4 L6 8 L10 4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
    </svg>
  </button>
  <Teleport to="body">
    <Transition name="u-dropdown">
      <ul
        v-if="open"
        ref="menuRef"
        class="u-dropdown"
        role="listbox"
        :aria-label="ariaLabel"
        tabindex="-1"
      >
        <li
          v-for="(it, idx) in items"
          :key="it.key"
          class="u-dropdown__item"
          :class="{
            'is-selected': it.key === modelValue,
            'is-disabled': it.disabled,
            'is-danger': it.danger
          }"
          role="option"
          :aria-selected="it.key === modelValue"
          :aria-disabled="it.disabled"
          tabindex="-1"
          @click="select(it.key)"
          @mouseenter="focusIdx = idx"
        >
          {{ it.label }}
        </li>
      </ul>
    </Transition>
  </Teleport>
</template>

<style scoped>
.u-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 32px;
  padding: 0 var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--font-sm);
  color: var(--color-text);
  cursor: pointer;
}

.u-dropdown-trigger:hover {
  border-color: var(--neutral-400);
}

.u-dropdown-trigger:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
}

.u-dropdown-trigger__label {
  flex: 1;
  text-align: left;
}

.u-dropdown-trigger__caret {
  transition: transform var(--motion-fast) var(--ease-out);
  color: var(--color-text-muted);
}

.u-dropdown-trigger__caret.is-open {
  transform: rotate(180deg);
}

.u-dropdown {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1450;
  min-width: 180px;
  padding: var(--space-1);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-dropdown);
  list-style: none;
  margin: 0;
}

.u-dropdown__item {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-sm);
  color: var(--color-text);
  cursor: pointer;
  user-select: none;
  outline: none;
}

.u-dropdown__item:hover,
.u-dropdown__item:focus {
  background: var(--neutral-100);
}

.u-dropdown__item.is-selected {
  color: var(--brand-700);
  font-weight: 500;
  background: var(--brand-50);
}

.u-dropdown__item.is-danger {
  color: var(--danger-500);
}

.u-dropdown__item.is-disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.u-dropdown-enter-from,
.u-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
.u-dropdown-enter-active,
.u-dropdown-leave-active {
  transition:
    opacity var(--motion-fast) var(--ease-out),
    transform var(--motion-fast) var(--ease-out);
}
</style>
