<script setup lang="ts">
// 单选框组件（[前端详细设计 §6.1 M0]）
// 实现口径：modelValue / value / label / name / disabled
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string | number | null | undefined
    value: string | number
    label?: string
    name?: string
    disabled?: boolean
  }>(),
  {
    disabled: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | number): void
  (e: 'change', value: string | number): void
}>()

const checked = computed<boolean>(() => props.modelValue === props.value)

function pick() {
  if (props.disabled) return
  emit('update:modelValue', props.value)
  emit('change', props.value)
}
</script>

<template>
  <label
    class="u-radio"
    :class="{ 'is-checked': checked, 'is-disabled': disabled }"
    @click.prevent="pick"
    @keydown.space.prevent="pick"
    tabindex="0"
    role="radio"
    :aria-checked="checked"
  >
    <span class="u-radio__dot" aria-hidden="true">
      <span v-if="checked" class="u-radio__inner" />
    </span>
    <span v-if="label || $slots.default" class="u-radio__label">
      <slot>{{ label }}</slot>
    </span>
  </label>
</template>

<style scoped>
.u-radio {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  user-select: none;
  font-size: var(--font-sm);
  color: var(--color-text);
  outline: 0;
}

.u-radio:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.u-radio.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.u-radio__dot {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--neutral-400);
  border-radius: var(--radius-full);
  background: var(--color-surface);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color var(--motion-base) var(--ease-out);
}

.u-radio.is-checked .u-radio__dot {
  border-color: var(--brand-500);
}

.u-radio__inner {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--brand-500);
}

.u-radio__label {
  line-height: 1.2;
}
</style>