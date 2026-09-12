<script setup lang="ts">
// 成本/Token 计量卡（[前端详细设计 §10.1 / §11.3 M2 最简版]）
// used/budget 进度条：70% 提示（warning）、90% danger；阈值以 cost.warning 事件级别为准，
// 事件级别缺省时按比率兜底推导。通道断连时仍展示最近快照并标注「数据可能滞后」。
// M2 只读：不提供升级/降级操作入口（对应 M2-5「仅内部」口径，操作面板在 M3/M4）。
import { computed } from 'vue'
import type { CostState } from '@/composables/useRunStream'
import { formatNumber } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    cost: CostState
    // 实时通道不可用（connecting/retrying）：数据可能滞后
    stale?: boolean
  }>(),
  { stale: false }
)

const percent = computed(() => Math.min(100, Math.round(props.cost.ratio * 100)))
const hasBudget = computed(() => props.cost.budget > 0)

// 预警级别：事件显式级别优先，缺省按比率推导（70%/90%）
const level = computed<'warning' | 'danger' | null>(() => {
  if (props.cost.warningLevel) return props.cost.warningLevel
  if (props.cost.ratio >= 0.9) return 'danger'
  if (props.cost.ratio >= 0.7) return 'warning'
  return null
})

const hint = computed<string>(() => {
  if (level.value === 'danger') return 'Token 用量已超过预算的 90%，即将达到上限'
  if (level.value === 'warning') return 'Token 用量已达预算的 70%，请注意成本'
  return ''
})
</script>

<template>
  <section class="cost-meter">
    <header class="cost-meter__head">
      <h3 class="cost-meter__title">
        实时成本
      </h3>
      <span
        v-if="stale"
        class="cost-meter__stale"
        role="status"
      >数据可能滞后</span>
    </header>

    <p class="cost-meter__numbers">
      <span class="cost-meter__used">{{ formatNumber(cost.used) }}</span>
      <span class="cost-meter__sep">/</span>
      <span class="cost-meter__budget">
        {{ hasBudget ? formatNumber(cost.budget) : '预算未知' }}
      </span>
      <span class="cost-meter__unit">Token</span>
      <span
        v-if="hasBudget"
        class="cost-meter__percent"
      >{{ percent }}%</span>
    </p>

    <div
      v-if="hasBudget"
      class="cost-meter__bar"
      role="img"
      :aria-label="`Token 用量 ${percent}%`"
    >
      <span
        class="cost-meter__fill"
        :class="level ? `is-${level}` : ''"
        :style="{ width: `${percent}%` }"
      />
    </div>

    <p
      v-if="hint"
      class="cost-meter__hint"
      :class="level ? `is-${level}` : ''"
      role="status"
    >
      {{ hint }}
    </p>

    <p
      v-if="stale"
      class="cost-meter__footnote"
    >
      实时连接中断，正在自动重连，当前显示为最近一次同步数据
    </p>
  </section>
</template>

<style scoped>
.cost-meter__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.cost-meter__title {
  margin: 0;
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.cost-meter__stale {
  font-size: var(--font-xs);
  color: var(--warning-500);
  background: var(--warning-50);
  border-radius: var(--radius-full);
  padding: 2px var(--space-2);
}

.cost-meter__numbers {
  display: flex;
  align-items: baseline;
  gap: var(--space-1);
  margin: 0 0 var(--space-3);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.cost-meter__used {
  font-size: var(--font-md);
  font-weight: 600;
  color: var(--color-text-strong);
}

.cost-meter__unit {
  margin-left: var(--space-1);
}

.cost-meter__percent {
  margin-left: auto;
  font-weight: 500;
}

.cost-meter__bar {
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--neutral-100);
  overflow: hidden;
}

.cost-meter__fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-full);
  background: var(--brand-500);
  transition:
    width 0.4s ease,
    background var(--motion-base) var(--ease-out);
}

.cost-meter__fill.is-warning {
  background: var(--warning-500);
}

.cost-meter__fill.is-danger {
  background: var(--danger-500);
}

.cost-meter__hint {
  margin: var(--space-2) 0 0;
  font-size: var(--font-xs);
}

.cost-meter__hint.is-warning {
  color: var(--warning-500);
}

.cost-meter__hint.is-danger {
  color: var(--danger-500);
}

.cost-meter__footnote {
  margin: var(--space-2) 0 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
</style>
