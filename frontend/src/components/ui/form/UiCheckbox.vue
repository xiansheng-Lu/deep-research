<script setup lang="ts">
// 复选框组件（[前端详细设计 §6.1 M0]）
// 实现口径：modelValue / label / indeterminate / disabled
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import { computed } from 'vue'

type CheckboxValue = string | number

const props = withDefaults(
  defineProps<{
    modelValue: boolean | CheckboxValue[]
    value?: CheckboxValue
    label?: string
    indeterminate?: boolean
    disabled?: boolean
  }>(),
  {
    indeterminate: false,
    disabled: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean | CheckboxValue[]): void
  (e: 'change', checked: boolean): void
}>()

const checked = computed<boolean>(() => {
  const m = props.modelValue
  if (Array.isArray(m)) {
    const v = props.value
    if (v === undefined) return false
    return m.includes(v)
  }
  return !!m
})

function toggle() {
  if (props.disabled) return
  const m = props.modelValue
  if (Array.isArray(m)) {
    const v = props.value
    if (v === undefined) return
    const exists = m.includes(v)
    const next = exists ? m.filter((x) => x !== v) : [...m, v]
    emit('update:modelValue', next)
    emit('change', !exists)
  } else {
    const next = !m
    emit('update:modelValue', next)
    emit('change', next)
  }
}
</script>

<template>
  <label
    class="u-checkbox"
    :class="{ 'is-checked': checked, 'is-indeterminate': indeterminate, 'is-disabled': disabled }"
    @click.prevent="toggle"
    @keydown.space.prevent="toggle"
    tabindex="0"
    role="checkbox"
    :aria-checked="indeterminate ? 'mixed' : checked"
  >
    <span class="u-checkbox__box" aria-hidden="true">
      <svg v-if="indeterminate" viewBox="0 0 12 12" class="u-checkbox__icon">
        <rect x="2" y="5.25" width="8" height="1.5" rx="0.75" fill="currentColor" />
      </svg>
      <svg v-else-if="checked" viewBox="0 0 12 12" class="u-checkbox__icon">
        <path
          d="M2.5 6.2 L5 8.5 L9.5 3.5"
          stroke="currentColor"
          stroke-width="1.6"
          fill="none"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </span>
    <span v-if="label || $slots.default" class="u-checkbox__label">
      <slot>{{ label }}</slot>
    </span>
  </label>
</template>

<style scoped>
.u-checkbox {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  user-select: none;
  font-size: var(--font-sm);
  color: var(--color-text);
  outline: 0;
}

.u-checkbox:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.u-checkbox.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.u-checkbox__box {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--neutral-400);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: transparent;
  transition:
    background var(--motion-base) var(--ease-out),
    border-color var(--motion-base) var(--ease-out);
}

.u-checkbox.is-checked .u-checkbox__box,
.u-checkbox.is-indeterminate .u-checkbox__box {
  background: var(--brand-500);
  border-color: var(--brand-500);
  color: var(--neutral-0);
}

.u-checkbox__icon {
  width: 12px;
  height: 12px;
}

.u-checkbox__label {
  line-height: 1.2;
}
</style>