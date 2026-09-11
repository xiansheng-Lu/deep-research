<script setup lang="ts">
// 骨架屏组件（[前端详细设计 §6.1 M0]）
// 实现口径：width / height / radius / count（加载 ≤3s 骨架屏）
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
type Radius = 'sm' | 'md' | 'lg' | 'full'

withDefaults(
  defineProps<{
    width?: string
    height?: string
    radius?: Radius
    count?: number
  }>(),
  {
    radius: 'sm',
    count: 1
  }
)
</script>

<template>
  <span class="u-skeleton-group">
    <span
      v-for="i in count"
      :key="i"
      class="u-skeleton"
      :class="[`u-skeleton--${radius}`]"
      :style="{
        width: width,
        height: height,
        display: count > 1 ? 'block' : 'inline-block'
      }"
      aria-hidden="true"
    />
  </span>
</template>

<style scoped>
.u-skeleton-group {
  display: inline-flex;
  flex-direction: column;
  gap: var(--space-1);
}

.u-skeleton {
  display: inline-block;
  background: linear-gradient(
    90deg,
    var(--neutral-100) 0%,
    var(--neutral-50) 50%,
    var(--neutral-100) 100%
  );
  background-size: 200% 100%;
  animation: u-skeleton-shimmer 1.4s ease-in-out infinite;
}

.u-skeleton--sm {
  border-radius: var(--radius-sm);
}
.u-skeleton--md {
  border-radius: var(--radius-md);
}
.u-skeleton--lg {
  border-radius: var(--radius-lg);
}
.u-skeleton--full {
  border-radius: var(--radius-full);
}

@keyframes u-skeleton-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>