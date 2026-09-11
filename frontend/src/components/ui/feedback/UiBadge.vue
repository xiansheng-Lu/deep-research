<script setup lang="ts">
// 徽章组件（[前端详细设计 §6.1 M0]）
// 实现口径：variant / size
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
type Variant = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'brand'
type Size = 'sm' | 'md'

withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
  }>(),
  {
    variant: 'neutral',
    size: 'sm'
  }
)
</script>

<template>
  <span
    class="u-badge"
    :class="[`u-badge--${variant}`, `u-badge--${size}`]"
  >
    <slot />
  </span>
</template>

<style scoped>
.u-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  height: 18px;
  border-radius: var(--radius-full);
  font-size: var(--font-xs);
  font-weight: 500;
  line-height: 1;
  border: 1px solid transparent;
  white-space: nowrap;
}

.u-badge--md {
  height: 22px;
  padding: 0 var(--space-3);
  font-size: var(--font-sm);
}

.u-badge--neutral {
  background: var(--neutral-100);
  color: var(--neutral-600);
}

.u-badge--info {
  background: rgba(61, 122, 184, 0.12);
  color: var(--info-500);
}

.u-badge--success {
  background: var(--success-50);
  color: var(--success-500);
}

.u-badge--warn {
  background: var(--warning-50);
  color: var(--warning-500);
}

.u-badge--danger {
  background: var(--danger-50);
  color: var(--danger-500);
}

.u-badge--brand {
  background: var(--brand-50);
  color: var(--brand-700);
}
</style>