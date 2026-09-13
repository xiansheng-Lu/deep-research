<script setup lang="ts">
// 报告末尾「研究局限与未决问题」汇总（[前端详细设计 §10.5 / §11.4]，M2 纯展示）：
// 汇总全部 type=limitation 的区块；无局限项时整块不渲染。
import { zhCN } from '@/services/i18n/zh-CN'
import type { ReportBlock } from '@/services/api/types'

defineProps<{
  blocks: ReportBlock[]
}>()
</script>

<template>
  <section
    v-if="blocks.length > 0"
    class="limitation-summary"
  >
    <h2 class="limitation-summary__title">
      {{ zhCN.report.limitationTitle }}
    </h2>
    <ul class="limitation-summary__list">
      <li
        v-for="(block, index) in blocks"
        :key="block.id ?? `limitation-${index}`"
        class="limitation-summary__item"
      >
        {{ block.text }}
      </li>
    </ul>
  </section>
</template>

<style scoped>
.limitation-summary {
  margin-top: var(--space-8);
  padding: var(--space-5) var(--space-6);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--neutral-50);
}

.limitation-summary__title {
  margin: 0 0 var(--space-3);
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.limitation-summary__list {
  margin: 0;
  padding-left: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.limitation-summary__item {
  font-size: var(--font-sm);
  line-height: 1.7;
  color: var(--neutral-600);
}
</style>
