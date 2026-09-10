<script setup lang="ts">
// 空状态组件（[前端详细设计 §6.1 M0]）
// 实现口径：title / hint / icon? / 默认主操作插槽
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
withDefaults(
  defineProps<{
    title?: string
    hint?: string
  }>(),
  {}
)
</script>

<template>
  <div
    class="u-empty"
    role="status"
  >
    <div
      class="u-empty__icon"
      aria-hidden="true"
    >
      <slot name="icon">
        <svg
          viewBox="0 0 64 64"
          class="u-empty__svg"
        >
          <circle
            cx="32"
            cy="32"
            r="28"
            stroke="currentColor"
            stroke-width="2"
            fill="none"
            opacity="0.3"
          />
          <path
            d="M22 32 H42 M32 22 V42"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            opacity="0.5"
          />
        </svg>
      </slot>
    </div>
    <h3
      v-if="title"
      class="u-empty__title"
    >
      {{ title }}
    </h3>
    <p
      v-if="hint || $slots.default"
      class="u-empty__hint"
    >
      <slot>{{ hint }}</slot>
    </p>
    <div
      v-if="$slots.action"
      class="u-empty__action"
    >
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.u-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: var(--space-12) var(--space-6);
  color: var(--color-text-muted);
}

.u-empty__icon {
  width: 64px;
  height: 64px;
  margin-bottom: var(--space-4);
  color: var(--neutral-400);
}

.u-empty__svg {
  width: 100%;
  height: 100%;
}

.u-empty__title {
  margin: 0;
  font-size: var(--font-base);
  font-weight: 500;
  color: var(--color-text-strong);
}

.u-empty__hint {
  margin: var(--space-2) 0 0;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  max-width: 360px;
}

.u-empty__action {
  margin-top: var(--space-6);
}
</style>