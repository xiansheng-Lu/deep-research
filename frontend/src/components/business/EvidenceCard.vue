<script setup lang="ts">
// 证据流卡片（[前端详细设计 §10.1 / §11.3 M2]）
// 紧凑行常态：标题 / 域名 / 一行摘要 + 信源角标；hover 或键盘聚焦展开次级元数据；
// 点击卡片懒加载全文（全文不进 WS/列表 payload，由页面经 useEvidenceList 拉取后透传）。
// 业务组件不直接发请求：content/加载态由 props 注入，展开时 emit toggle 交页面编排。
// 剔除证据（✕）与可恢复列在 WP-15，本组件不提供入口。
import { computed } from 'vue'
import type { EvidenceResponse } from '@/services/api/types'
import { formatDateTime } from '@/utils/format'
import SourceBadge from '@/components/business/SourceBadge.vue'

const props = withDefaults(
  defineProps<{
    evidence: EvidenceResponse
    expanded?: boolean
    // 懒加载全文（useEvidenceList.contents 透传；null=尚未加载）
    content?: string | null
    contentLoading?: boolean
    contentError?: string
    // 子问题归属标签（如「子问题 1」），由页面按证据 id 反查
    sqLabel?: string | null
  }>(),
  {
    expanded: false,
    content: null,
    contentLoading: false,
    contentError: undefined,
    sqLabel: null
  }
)

const emit = defineEmits<{
  (e: 'toggle', evidenceId: string): void
}>()

const relevancePercent = computed(() =>
  Math.round((props.evidence.relevance_score ?? 0) * 100)
)

function onToggle(): void {
  emit('toggle', props.evidence.id)
}
</script>

<template>
  <article
    class="evidence-card"
    :class="{ 'is-expanded': expanded }"
  >
    <div class="evidence-card__main">
      <button
        type="button"
        class="evidence-card__head"
        :aria-expanded="expanded"
        @click="onToggle"
      >
        <span
          class="evidence-card__title"
          :title="evidence.title"
        >{{ evidence.title }}</span>
        <svg
          class="evidence-card__chevron"
          :class="{ 'is-open': expanded }"
          viewBox="0 0 16 16"
          width="12"
          height="12"
          aria-hidden="true"
        >
          <path
            d="M4 6 L8 10 L12 6"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>

      <p class="evidence-card__snippet">
        {{ evidence.snippet }}
      </p>

      <div class="evidence-card__meta">
        <SourceBadge
          :domain="evidence.domain"
          :source-type="evidence.source_type"
          :source-level="evidence.source_level"
          :credibility="evidence.credibility"
        />
        <span
          v-if="evidence.published_at"
          class="evidence-card__date"
        >{{ formatDateTime(evidence.published_at) }}</span>
        <span
          v-else
          class="evidence-card__date evidence-card__date--unknown"
        >发布时间不详</span>
        <span
          v-if="sqLabel"
          class="evidence-card__sq"
        >{{ sqLabel }}</span>
      </div>

      <!-- hover/聚焦展开的次级元数据（点击展开全文时同样常驻可见） -->
      <div class="evidence-card__more">
        <span>相关性 {{ relevancePercent }}%</span>
        <a
          :href="evidence.url"
          class="evidence-card__link"
          target="_blank"
          rel="noopener noreferrer"
          :title="evidence.url"
          @click.stop
        >原文链接</a>
      </div>
    </div>

    <div
      v-if="expanded"
      class="evidence-card__detail"
    >
      <template v-if="contentLoading">
        <p class="evidence-card__detail-hint">
          全文加载中…
        </p>
      </template>
      <template v-else-if="contentError">
        <p class="evidence-card__detail-error">
          {{ contentError }}
        </p>
      </template>
      <template v-else-if="content">
        <p class="evidence-card__content">
          {{ content }}
        </p>
      </template>
      <template v-else>
        <p class="evidence-card__detail-hint">
          暂无全文内容
        </p>
      </template>
    </div>
  </article>
</template>

<style scoped>
.evidence-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  padding: var(--space-3) var(--space-4);
  transition: border-color var(--motion-base) var(--ease-out);
}

.evidence-card:hover,
.evidence-card:focus-within,
.evidence-card.is-expanded {
  border-color: var(--neutral-400);
}

.evidence-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
  width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.evidence-card__title {
  font-size: var(--font-sm);
  font-weight: 500;
  line-height: 1.5;
  color: var(--color-text-strong);
}

.evidence-card__chevron {
  flex: 0 0 auto;
  margin-top: 4px;
  color: var(--color-text-muted);
  transition: transform var(--motion-fast) var(--ease-out);
}

.evidence-card__chevron.is-open {
  transform: rotate(180deg);
}

.evidence-card__snippet {
  margin: var(--space-1) 0 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.evidence-card__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.evidence-card__date {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.evidence-card__date--unknown {
  color: var(--warning-500);
}

.evidence-card__sq {
  font-size: var(--font-xs);
  color: var(--brand-700);
  background: var(--brand-50);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-1);
}

/* 次级元数据：hover/聚焦/展开时显现，避免常态信息过载 */
.evidence-card__more {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: 0;
  max-height: 0;
  opacity: 0;
  overflow: hidden;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  transition:
    max-height var(--motion-base) var(--ease-out),
    opacity var(--motion-base) var(--ease-out),
    margin-top var(--motion-base) var(--ease-out);
}

.evidence-card:hover .evidence-card__more,
.evidence-card:focus-within .evidence-card__more,
.evidence-card.is-expanded .evidence-card__more {
  margin-top: var(--space-2);
  max-height: 24px;
  opacity: 1;
}

.evidence-card__link {
  color: var(--brand-700);
  text-decoration: none;
}

.evidence-card__link:hover {
  text-decoration: underline;
}

.evidence-card__detail {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
}

.evidence-card__content {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.7;
  color: var(--color-text);
  white-space: pre-line;
  word-break: break-word;
}

.evidence-card__detail-hint {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.evidence-card__detail-error {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--danger-500);
}
</style>
