<script setup lang="ts">
// 报告页工具条（[前端详细设计 §10.1 / §11.4] M2）：
// 返回 + 标题/元数据（档位/生成时间/token）+「只看分歧」筛选开关。
// 分享/导出/更多操作均为 M4 入口，本 WP 一律不出现、不预埋。
import { RouterLink } from 'vue-router'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiSwitch from '@/components/ui/form/UiSwitch.vue'
import { tierLabel, zhCN } from '@/services/i18n/zh-CN'
import { formatDateTime, formatNumber } from '@/utils/format'
import type { RunTier } from '@/types/domain'

defineProps<{
  backHref: string
  question: string
  tier: RunTier
  createdAt: string
  tokenUsed: number
  disputesOnly: boolean
}>()

const emit = defineEmits<{
  (e: 'update:disputesOnly', value: boolean): void
}>()

function setFilter(value: boolean): void {
  emit('update:disputesOnly', value)
}
</script>

<template>
  <header class="report-chrome">
    <RouterLink
      :to="backHref"
      class="report-chrome__back"
    >
      返回指挥舱
    </RouterLink>

    <div class="report-chrome__main">
      <h1
        class="report-chrome__title"
        :title="question"
      >
        {{ question }}
      </h1>
      <div class="report-chrome__bar">
        <div class="report-chrome__meta">
          <UiBadge variant="brand">
            {{ tierLabel(tier) }}
          </UiBadge>
          <span>生成时间：{{ formatDateTime(createdAt) }}</span>
          <span>Token 用量：{{ formatNumber(tokenUsed) }}</span>
        </div>
        <div class="report-chrome__filter">
          <UiSwitch
            :model-value="disputesOnly"
            @update:model-value="setFilter"
          >
            {{ zhCN.report.filterDisputesOnly }}
          </UiSwitch>
          <button
            v-if="disputesOnly"
            type="button"
            class="report-chrome__show-all"
            @click="setFilter(false)"
          >
            {{ zhCN.report.filterShowAll }}
          </button>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.report-chrome {
  margin-bottom: var(--space-6);
}

.report-chrome__back {
  display: inline-block;
  margin-bottom: var(--space-4);
  font-size: var(--font-sm);
  color: var(--neutral-600);
}

.report-chrome__back:hover {
  color: var(--brand-700);
}

.report-chrome__main {
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.report-chrome__title {
  margin: 0 0 var(--space-3);
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  line-height: 1.4;
  color: var(--color-text-strong);
}

.report-chrome__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.report-chrome__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-4);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.report-chrome__filter {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-xs);
}

.report-chrome__show-all {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--brand-700);
  font-size: var(--font-xs);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
</style>
