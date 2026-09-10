<script setup lang="ts">
// 标签页组件（[前端详细设计 §6.1 M0 导航]）
// 实现口径：modelValue / tabs: {key,label,disabled}[] / 下划线样式 / 受控+非受控
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import { computed, ref } from 'vue'

export interface TabItem {
  key: string
  label: string
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue?: string
    tabs: TabItem[]
    variant?: 'line' | 'pill'
    ariaLabel?: string
  }>(),
  {
    variant: 'line'
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', key: string): void
  (e: 'change', key: string): void
}>()

// 非受控内部值
const internalValue = ref<string>(
  props.modelValue ?? props.tabs.find((t) => !t.disabled)?.key ?? ''
)

// 同步外部 modelValue 到内部
const current = computed<string>(() => props.modelValue ?? internalValue.value)

function select(key: string): void {
  const tab = props.tabs.find((t) => t.key === key)
  if (!tab || tab.disabled) return
  if (props.modelValue === undefined) internalValue.value = key
  emit('update:modelValue', key)
  emit('change', key)
}

// 键盘导航：ArrowLeft/Right 循环；Home/End 跳首尾；跳过 disabled
function onKey(e: KeyboardEvent, idx: number): void {
  if (props.tabs.length === 0) return
  const enabledIdxs = props.tabs
    .map((t, i) => (t.disabled ? -1 : i))
    .filter((i) => i >= 0)
  if (enabledIdxs.length === 0) return
  const cur = enabledIdxs.indexOf(idx)

  let nextIdx: number | null = null
  switch (e.key) {
    case 'ArrowRight':
      nextIdx = enabledIdxs[(cur + 1) % enabledIdxs.length]
      break
    case 'ArrowLeft':
      nextIdx = enabledIdxs[(cur - 1 + enabledIdxs.length) % enabledIdxs.length]
      break
    case 'Home':
      nextIdx = enabledIdxs[0]
      break
    case 'End':
      nextIdx = enabledIdxs[enabledIdxs.length - 1]
      break
    default:
      return
  }
  e.preventDefault()
  const target = props.tabs[nextIdx]
  if (target) select(target.key)
}
</script>

<template>
  <div
    class="u-tabs"
    :class="[`u-tabs--${variant}`]"
    role="tablist"
    :aria-label="ariaLabel"
  >
    <button
      v-for="(tab, idx) in tabs"
      :key="tab.key"
      type="button"
      role="tab"
      class="u-tabs__item"
      :class="{
        'is-active': tab.key === current,
        'is-disabled': tab.disabled
      }"
      :aria-selected="tab.key === current"
      :aria-controls="`u-tabpanel-${tab.key}`"
      :tabindex="tab.key === current ? 0 : -1"
      :disabled="tab.disabled"
      @click="select(tab.key)"
      @keydown="onKey($event, idx)"
    >
      {{ tab.label }}
    </button>
  </div>
</template>

<style scoped>
.u-tabs {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

/* ── line 变体：底部边框 + 激活下划线 ── */
.u-tabs--line {
  gap: var(--space-4);
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0;
}

.u-tabs--line .u-tabs__item {
  position: relative;
  padding: var(--space-3) var(--space-2);
  font-size: var(--font-sm);
  font-weight: 400;
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  transition: color var(--motion-fast) var(--ease-out),
    border-color var(--motion-fast) var(--ease-out);
}

.u-tabs--line .u-tabs__item:hover:not(.is-disabled):not(.is-active) {
  color: var(--color-text);
}

.u-tabs--line .u-tabs__item.is-active {
  color: var(--brand-700);
  font-weight: 500;
  border-bottom-color: var(--brand-500);
}

/* ── pill 变体：胶囊背景 ── */
.u-tabs--pill {
  gap: var(--space-1);
  background: var(--neutral-100);
  padding: var(--space-1);
  border-radius: var(--radius-md);
}

.u-tabs--pill .u-tabs__item {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-sm);
  font-weight: 400;
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--motion-fast) var(--ease-out),
    color var(--motion-fast) var(--ease-out);
}

.u-tabs--pill .u-tabs__item:hover:not(.is-disabled):not(.is-active) {
  color: var(--color-text);
}

.u-tabs--pill .u-tabs__item.is-active {
  background: var(--color-surface);
  color: var(--color-text-strong);
  font-weight: 500;
  box-shadow: var(--shadow-popover);
}

/* ── 共用：disabled 态 ── */
.u-tabs__item.is-disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.u-tabs__item:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
</style>
