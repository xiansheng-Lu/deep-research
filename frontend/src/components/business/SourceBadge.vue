<script setup lang="ts">
// 信源角标（[前端详细设计 §10.1 / §10.5 M2]）
// 紧凑呈现信源三要素：可信分级（A/B/C/D 色编码字母）、信源类型、域名；
// 信源层级（一手/二手/三手）以小字附在类型后。供证据卡、冲突块、报告溯源共用。
import {
  credibilityLabel,
  sourceLevelLabel,
  sourceTypeLabel
} from '@/services/i18n/zh-CN'
import type { Credibility, SourceLevel, SourceType } from '@/types/domain'

withDefaults(
  defineProps<{
    domain: string
    sourceType: SourceType
    sourceLevel: SourceLevel
    credibility: Credibility
    // 是否展示域名（冲突块等窄空间可关）
    showDomain?: boolean
  }>(),
  { showDomain: true }
)
</script>

<template>
  <span
    class="source-badge"
    :title="`${sourceTypeLabel(sourceType)} · ${sourceLevelLabel(sourceLevel)} · ${credibilityLabel(credibility)}`"
  >
    <span
      class="source-badge__grade"
      :class="`is-${credibility}`"
      aria-hidden="true"
    >{{ credibility }}</span>
    <span class="source-badge__type">{{ sourceTypeLabel(sourceType) }}</span>
    <span class="source-badge__level">· {{ sourceLevelLabel(sourceLevel) }}</span>
    <span
      v-if="showDomain"
      class="source-badge__domain"
    >{{ domain }}</span>
  </span>
</template>

<style scoped>
.source-badge {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  min-width: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.source-badge__grade {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}

.source-badge__grade.is-A {
  background: var(--success-50);
  color: var(--success-500);
}

.source-badge__grade.is-B {
  background: var(--success-50);
  color: var(--success-500);
}

.source-badge__grade.is-C {
  background: var(--neutral-100);
  color: var(--neutral-600);
}

.source-badge__grade.is-D {
  background: var(--warning-50);
  color: var(--warning-500);
}

.source-badge__type {
  color: var(--color-text);
}

.source-badge__level {
  color: var(--color-text-muted);
}

.source-badge__domain {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
