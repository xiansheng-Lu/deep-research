<script setup lang="ts">
// 基础按钮组件骨架（[前端详细设计 §6.1 M0]）
// 实现口径：variant / size / loading / disabled / icon / nativeType
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md' | 'lg'

withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    loading?: boolean
    disabled?: boolean
    nativeType?: 'button' | 'submit' | 'reset'
  }>(),
  {
    variant: 'primary',
    size: 'md',
    loading: false,
    disabled: false,
    nativeType: 'button'
  }
)

defineEmits<{
  (e: 'click', ev: MouseEvent): void
}>()
</script>

<template>
  <button
    class="u-btn"
    :class="[`u-btn--${variant}`, `u-btn--${size}`, { 'is-loading': loading }]"
    :disabled="disabled || loading"
    :type="nativeType"
    @click="(ev) => $emit('click', ev)"
  >
    <span v-if="loading" class="u-btn__spinner" aria-hidden="true" />
    <slot name="icon" />
    <slot />
  </button>
</template>

<style scoped>
.u-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: 0 var(--space-4);
  height: 36px;
  border-radius: var(--radius-sm);
  font-size: var(--font-sm);
  font-weight: 500;
  border: 1px solid transparent;
  cursor: pointer;
  transition:
    background var(--motion-base) var(--ease-out),
    border-color var(--motion-base) var(--ease-out),
    color var(--motion-base) var(--ease-out);
  white-space: nowrap;
  color: inherit;
}

.u-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.u-btn--sm {
  height: 28px;
  padding: 0 var(--space-3);
  font-size: var(--font-xs);
}

.u-btn--lg {
  height: 44px;
  padding: 0 var(--space-6);
  font-size: var(--font-base);
}

.u-btn--primary {
  background: var(--brand-500);
  color: var(--neutral-0);
}
.u-btn--primary:hover:not(:disabled) {
  background: var(--brand-700);
}

.u-btn--secondary {
  background: var(--color-surface);
  color: var(--neutral-800);
  border-color: var(--color-border);
}
.u-btn--secondary:hover:not(:disabled) {
  border-color: var(--neutral-400);
  background: var(--neutral-50);
}

.u-btn--ghost {
  background: transparent;
  color: var(--neutral-600);
}
.u-btn--ghost:hover:not(:disabled) {
  background: var(--neutral-100);
  color: var(--color-text-strong);
}

.u-btn--danger {
  background: var(--danger-500);
  color: var(--neutral-0);
}
.u-btn--danger:hover:not(:disabled) {
  background: #a33a3a;
}

.u-btn__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: var(--radius-full);
  animation: u-btn-spin 700ms linear infinite;
}

@keyframes u-btn-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
