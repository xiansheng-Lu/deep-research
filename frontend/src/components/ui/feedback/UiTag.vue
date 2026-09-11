<script setup lang="ts">
// 标签组件（[前端详细设计 §6.1 M0]）
// 实现口径：closable / icon?
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
type Variant = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'brand'

withDefaults(
  defineProps<{
    variant?: Variant
    closable?: boolean
  }>(),
  {
    variant: 'neutral',
    closable: false
  }
)

const emit = defineEmits<{
  (e: 'close'): void
}>()
</script>

<template>
  <span
    class="u-tag"
    :class="[`u-tag--${variant}`]"
  >
    <slot name="icon" />
    <span class="u-tag__label"><slot /></span>
    <button
      v-if="closable"
      class="u-tag__close"
      type="button"
      aria-label="关闭标签"
      @click="emit('close')"
    >
      <svg
        viewBox="0 0 10 10"
        class="u-tag__close-icon"
        aria-hidden="true"
      >
        <path
          d="M2 2 L8 8 M8 2 L2 8"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linecap="round"
        />
      </svg>
    </button>
  </span>
</template>

<style scoped>
.u-tag {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--font-xs);
  line-height: 1.4;
  border: 1px solid transparent;
  white-space: nowrap;
}

.u-tag__label {
  flex: 1;
  min-width: 0;
}

.u-tag__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border: 0;
  background: transparent;
  cursor: pointer;
  border-radius: var(--radius-sm);
  color: inherit;
  padding: 0;
}

.u-tag__close:hover {
  background: rgba(15, 17, 23, 0.08);
}

.u-tag__close-icon {
  width: 10px;
  height: 10px;
}

.u-tag--neutral {
  background: var(--neutral-50);
  color: var(--neutral-600);
  border-color: var(--neutral-200);
}

.u-tag--info {
  background: rgba(61, 122, 184, 0.1);
  color: var(--info-500);
  border-color: rgba(61, 122, 184, 0.3);
}

.u-tag--success {
  background: var(--success-50);
  color: var(--success-500);
  border-color: rgba(47, 158, 107, 0.3);
}

.u-tag--warn {
  background: var(--warning-50);
  color: var(--warning-500);
  border-color: rgba(208, 138, 46, 0.3);
}

.u-tag--danger {
  background: var(--danger-50);
  color: var(--danger-500);
  border-color: rgba(200, 74, 74, 0.3);
}

.u-tag--brand {
  background: var(--brand-50);
  color: var(--brand-700);
  border-color: rgba(61, 71, 137, 0.3);
}
</style>