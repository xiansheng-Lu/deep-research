<script setup lang="ts">
// 开关组件（[前端详细设计 §6.1 M0]）
// 实现口径：modelValue / label / disabled
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    label?: string
    disabled?: boolean
  }>(),
  {
    disabled: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'change', value: boolean): void
}>()

function toggle() {
  if (props.disabled) return
  const next = !props.modelValue
  emit('update:modelValue', next)
  emit('change', next)
}
</script>

<template>
  <label
    class="u-switch"
    :class="{ 'is-checked': modelValue, 'is-disabled': disabled }"
    @click.prevent="toggle"
    @keydown.space.prevent="toggle"
    tabindex="0"
    role="switch"
    :aria-checked="modelValue"
  >
    <span class="u-switch__track" aria-hidden="true">
      <span class="u-switch__thumb" />
    </span>
    <span v-if="label || $slots.default" class="u-switch__label">
      <slot>{{ label }}</slot>
    </span>
  </label>
</template>

<style scoped>
.u-switch {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  user-select: none;
  font-size: var(--font-sm);
  color: var(--color-text);
  outline: 0;
}

.u-switch:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.u-switch.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.u-switch__track {
  position: relative;
  width: 32px;
  height: 18px;
  border-radius: var(--radius-full);
  background: var(--neutral-200);
  transition: background var(--motion-base) var(--ease-out);
}

.u-switch__thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: var(--radius-full);
  background: var(--neutral-0);
  box-shadow: 0 1px 2px rgba(15, 17, 23, 0.2);
  transition: left var(--motion-base) var(--ease-out);
}

.u-switch.is-checked .u-switch__track {
  background: var(--brand-500);
}

.u-switch.is-checked .u-switch__thumb {
  left: 16px;
}

.u-switch__label {
  line-height: 1.2;
}
</style>