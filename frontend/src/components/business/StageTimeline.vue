<script setup lang="ts">
// 六阶段时间线（[前端详细设计 §10.1 / §11.3]）
// WP-14 由 CockpitView 内联实现原样抽离为业务组件（搬移不改写）：
// 纵向节点按 pending/running/done/failed 四态着色，running 节点带呼吸动效。
import { stageLabel } from '@/services/i18n/zh-CN'
import type { StageRuntime } from '@/composables/useRunStream'

defineProps<{
  stages: StageRuntime[]
}>()

// 时间线节点状态文案
const STAGE_STATUS_TEXT: Record<StageRuntime['status'], string> = {
  pending: '待执行',
  running: '进行中',
  done: '已完成',
  failed: '失败'
}
</script>

<template>
  <ol class="stage-timeline">
    <li
      v-for="stage in stages"
      :key="stage.name"
      class="stage-timeline__item"
      :class="`is-${stage.status}`"
    >
      <span
        class="stage-timeline__dot"
        aria-hidden="true"
      />
      <div class="stage-timeline__body">
        <p class="stage-timeline__name">
          {{ stageLabel(stage.name) }}
        </p>
        <p class="stage-timeline__meta">
          <span>{{ STAGE_STATUS_TEXT[stage.status] }}</span>
          <span
            v-if="stage.attempt !== null && stage.status !== 'pending'"
            class="stage-timeline__attempt"
          >
            · 第 {{ stage.attempt }} 次尝试
          </span>
        </p>
        <p
          v-if="stage.status === 'failed' && stage.errorMessage"
          class="stage-timeline__error"
        >
          {{ stage.errorMessage }}
        </p>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.stage-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}

.stage-timeline__item {
  position: relative;
  display: flex;
  gap: var(--space-4);
  padding-bottom: var(--space-6);
}

.stage-timeline__item::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 18px;
  bottom: -2px;
  width: 2px;
  background: var(--neutral-200);
}

.stage-timeline__item:last-child {
  padding-bottom: 0;
}

.stage-timeline__item:last-child::before {
  display: none;
}

.stage-timeline__dot {
  position: relative;
  z-index: 1;
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  margin-top: 3px;
  border-radius: 50%;
  border: 2px solid var(--neutral-400);
  background: var(--color-surface);
  box-sizing: border-box;
}

.stage-timeline__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.stage-timeline__name {
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text-muted);
}

.stage-timeline__meta {
  display: flex;
  gap: var(--space-1);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.stage-timeline__attempt {
  white-space: pre;
}

.stage-timeline__error {
  margin: 2px 0 0;
  font-size: var(--font-xs);
  color: var(--danger-500);
  word-break: break-word;
}

.stage-timeline__item.is-done .stage-timeline__dot {
  border-color: var(--success-500);
  background: var(--success-500);
}

.stage-timeline__item.is-done .stage-timeline__name {
  color: var(--color-text);
}

.stage-timeline__item.is-running .stage-timeline__dot {
  border-color: var(--brand-500);
  background: var(--brand-500);
  animation: stage-pulse 1.6s ease-in-out infinite;
}

.stage-timeline__item.is-running .stage-timeline__name {
  color: var(--color-text-strong);
}

.stage-timeline__item.is-running .stage-timeline__meta {
  color: var(--brand-700);
}

.stage-timeline__item.is-failed .stage-timeline__dot {
  border-color: var(--danger-500);
  background: var(--danger-500);
}

.stage-timeline__item.is-failed .stage-timeline__name,
.stage-timeline__item.is-failed .stage-timeline__meta {
  color: var(--danger-500);
}

@keyframes stage-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}
</style>
