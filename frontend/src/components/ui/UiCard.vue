<script setup lang="ts">
// 基础卡片（[前端详细设计 §6.1 M0]）
// 边框卡片，不用阴影区分层级（DS §关键约束）
withDefaults(
  defineProps<{
    size?: 'md' | 'lg'
    interactive?: boolean
  }>(),
  { size: 'md', interactive: false }
)
</script>

<template>
  <div
    class="u-card"
    :class="[`u-card--${size}`, { 'is-interactive': interactive }]"
    :tabindex="interactive ? 0 : undefined"
  >
    <slot />
  </div>
</template>

<style scoped>
.u-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-6);
  transition:
    border-color var(--motion-base) var(--ease-out),
    background var(--motion-base) var(--ease-out);
}

.u-card--lg {
  border-radius: var(--radius-lg);
  padding: var(--space-8);
}

.u-card.is-interactive {
  cursor: pointer;
}

.u-card.is-interactive:hover {
  border-color: var(--neutral-400);
}
</style>
