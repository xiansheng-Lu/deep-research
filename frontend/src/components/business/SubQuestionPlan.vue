<script setup lang="ts">
// 子问题计划（只读，[前端详细设计 §10.1 / §11.3 M2]）
// 展示拆解出的子问题队列与 m/n 完成进度；evidence_short（证据不足）走 warn 显式提示，
// 不得冒充成功结论（PRD A9）。编辑/排序/增删为 M3 能力，本组件不提供入口。
import { computed } from 'vue'
import type { SubQuestionResponse } from '@/services/api/types'
import {
  subQuestionStatusLabel,
  subQuestionStatusVariant
} from '@/services/i18n/zh-CN'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'

const props = defineProps<{
  subQuestions: SubQuestionResponse[]
}>()

// m/n：成功完成数 / 子问题总数（与 useRunStream 派生口径一致，组件内自算免外部透传）
const completed = computed(
  () => props.subQuestions.filter((item) => item.status === 'succeeded').length
)
const evidenceShortCount = computed(
  () => props.subQuestions.filter((item) => item.status === 'evidence_short').length
)
const failedCount = computed(
  () => props.subQuestions.filter((item) => item.status === 'failed').length
)
</script>

<template>
  <section class="sub-question-plan">
    <header class="sub-question-plan__head">
      <h3 class="sub-question-plan__title">
        子问题计划
      </h3>
      <UiBadge
        v-if="subQuestions.length > 0"
        :variant="completed === subQuestions.length ? 'success' : 'info'"
      >
        {{ completed }}/{{ subQuestions.length }}
      </UiBadge>
    </header>

    <p
      v-if="subQuestions.length === 0"
      class="sub-question-plan__empty"
    >
      问题分解尚未开始，子问题将在「问题分解」阶段产生
    </p>

    <ol
      v-else
      class="sub-question-plan__list"
    >
      <li
        v-for="(item, index) in subQuestions"
        :key="item.id"
        class="sub-question-plan__item"
        :class="[`is-${item.status}`]"
      >
        <span class="sub-question-plan__index">{{ index + 1 }}</span>
        <div class="sub-question-plan__body">
          <p
            class="sub-question-plan__question"
            :title="item.question"
          >
            {{ item.question }}
          </p>
          <div class="sub-question-plan__meta">
            <UiBadge :variant="subQuestionStatusVariant(item.status)">
              {{ subQuestionStatusLabel(item.status) }}
            </UiBadge>
            <span
              v-if="item.status === 'succeeded' || item.evidence_count > 0"
              class="sub-question-plan__evidence"
            >
              证据 {{ item.evidence_count }} 条
            </span>
            <span
              v-if="item.status === 'evidence_short'"
              class="sub-question-plan__warn"
            >
              该子问题可用证据不足，结论将受限
            </span>
          </div>
        </div>
      </li>
    </ol>

    <p
      v-if="evidenceShortCount + failedCount > 0"
      class="sub-question-plan__summary"
    >
      <template v-if="evidenceShortCount > 0">
        {{ evidenceShortCount }} 个子问题证据不足
      </template>
      <template v-if="evidenceShortCount > 0 && failedCount > 0">
        ；
      </template>
      <template v-if="failedCount > 0">
        {{ failedCount }} 个子问题执行失败
      </template>
    </p>
  </section>
</template>

<style scoped>
.sub-question-plan__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.sub-question-plan__title {
  margin: 0;
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.sub-question-plan__empty {
  margin: 0;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}

.sub-question-plan__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.sub-question-plan__item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.sub-question-plan__index {
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  margin-top: 1px;
  border-radius: var(--radius-full);
  background: var(--neutral-100);
  color: var(--neutral-600);
  font-size: var(--font-xs);
  line-height: 20px;
  text-align: center;
}

.sub-question-plan__item.is-succeeded .sub-question-plan__index {
  background: var(--success-50);
  color: var(--success-500);
}

.sub-question-plan__item.is-running .sub-question-plan__index {
  background: var(--brand-50);
  color: var(--brand-700);
}

.sub-question-plan__item.is-evidence_short .sub-question-plan__index,
.sub-question-plan__item.is-failed .sub-question-plan__index {
  background: var(--warning-50);
  color: var(--warning-500);
}

.sub-question-plan__item.is-failed .sub-question-plan__index {
  background: var(--danger-50);
  color: var(--danger-500);
}

.sub-question-plan__body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.sub-question-plan__question {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.5;
  color: var(--color-text);
}

.sub-question-plan__item.is-evidence_short .sub-question-plan__question {
  color: var(--color-text);
}

.sub-question-plan__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.sub-question-plan__evidence {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.sub-question-plan__warn {
  font-size: var(--font-xs);
  color: var(--warning-500);
}

.sub-question-plan__summary {
  margin: var(--space-3) 0 0;
  font-size: var(--font-xs);
  color: var(--warning-500);
}
</style>
