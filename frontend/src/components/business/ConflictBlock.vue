<script setup lang="ts">
// 冲突/分歧内联警告块（[前端详细设计 §10.1 / §10.5 M2]）
// M2 口径：只读呈现与提示——红点摘要可展开查看双方证据立场，不提供任何裁决操作
// （裁决面板 VerdictPanel 与分歧工作台入口在 M3，本组件不预埋按钮）。
import { ref } from 'vue'
import type { ConflictDetailResponse, ConflictResponse, EvidenceResponse } from '@/services/api/types'

// 证据来源：证据池完整行，或 M2-2 分歧详情内嵌的摘要（无 source_level 等字段）
type ConflictEvidenceLike = EvidenceResponse | ConflictDetailResponse['evidence_a']
import {
  conflictSeverityLabel,
  conflictSeverityVariant,
  conflictStatusLabel,
  conflictStatusVariant,
  conflictTypeLabel
} from '@/services/i18n/zh-CN'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import SourceBadge from '@/components/business/SourceBadge.vue'

const props = withDefaults(
  defineProps<{
    conflict: ConflictResponse
    // 双方证据：优先证据池完整行，缺失时由页面按分歧详情内嵌摘要兜底
    evidenceA?: ConflictEvidenceLike | null
    evidenceB?: ConflictEvidenceLike | null
    defaultExpanded?: boolean
  }>(),
  {
    evidenceA: null,
    evidenceB: null,
    defaultExpanded: false
  }
)

const emit = defineEmits<{
  // 展开态变化：页面据此懒加载分歧详情
  (e: 'toggle', conflictId: string, expanded: boolean): void
}>()

const expanded = ref(false)

function toggle(): void {
  expanded.value = !expanded.value
  emit('toggle', props.conflict.id, expanded.value)
}
</script>

<template>
  <article class="conflict-block">
    <button
      type="button"
      class="conflict-block__head"
      :aria-expanded="expanded || defaultExpanded"
      @click="toggle"
    >
      <span
        class="conflict-block__icon"
        aria-hidden="true"
      >!</span>
      <span class="conflict-block__summary">
        <span
          class="conflict-block__claim"
          :title="conflict.claim"
        >{{ conflict.claim }}</span>
        <span class="conflict-block__badges">
          <UiBadge variant="neutral">
            {{ conflictTypeLabel(conflict.type) }}
          </UiBadge>
          <UiBadge :variant="conflictSeverityVariant(conflict.severity)">
            {{ conflictSeverityLabel(conflict.severity) }}
          </UiBadge>
          <UiBadge :variant="conflictStatusVariant(conflict.status)">
            {{ conflictStatusLabel(conflict.status) }}
          </UiBadge>
        </span>
      </span>
      <svg
        class="conflict-block__chevron"
        :class="{ 'is-open': expanded || defaultExpanded }"
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

    <div
      v-if="expanded || defaultExpanded"
      class="conflict-block__detail"
    >
      <div class="conflict-block__sides">
        <div class="conflict-block__side">
          <p class="conflict-block__side-label">
            立场 A
          </p>
          <template v-if="evidenceA">
            <p class="conflict-block__side-title">
              {{ evidenceA.title }}
            </p>
            <SourceBadge
              :domain="evidenceA.domain"
              :source-type="evidenceA.source_type"
              :source-level="'source_level' in evidenceA ? evidenceA.source_level : undefined"
              :credibility="evidenceA.credibility"
            />
          </template>
          <p
            v-else
            class="conflict-block__side-fallback"
          >
            证据 {{ conflict.evidence_a_id }}（元数据待加载）
          </p>
        </div>
        <div class="conflict-block__side">
          <p class="conflict-block__side-label">
            立场 B
          </p>
          <template v-if="evidenceB">
            <p class="conflict-block__side-title">
              {{ evidenceB.title }}
            </p>
            <SourceBadge
              :domain="evidenceB.domain"
              :source-type="evidenceB.source_type"
              :source-level="'source_level' in evidenceB ? evidenceB.source_level : undefined"
              :credibility="evidenceB.credibility"
            />
          </template>
          <p
            v-else
            class="conflict-block__side-fallback"
          >
            证据 {{ conflict.evidence_b_id }}（元数据待加载）
          </p>
        </div>
      </div>
      <p class="conflict-block__note">
        M2 阶段仅提示冲突存在并保留双方立场，裁决能力将在后续版本开放
      </p>
    </div>
  </article>
</template>

<style scoped>
.conflict-block {
  border: 1px solid var(--danger-500);
  border-radius: var(--radius-md);
  background: var(--danger-50);
  overflow: hidden;
}

.conflict-block__head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.conflict-block__icon {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  border-radius: var(--radius-full);
  background: var(--danger-500);
  color: var(--neutral-0);
  font-size: var(--font-xs);
  font-weight: 700;
  font-style: italic;
  line-height: 1;
}

.conflict-block__summary {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.conflict-block__claim {
  font-size: var(--font-sm);
  line-height: 1.5;
  color: var(--color-text-strong);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.conflict-block__badges {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.conflict-block__chevron {
  flex: 0 0 auto;
  margin-top: 4px;
  color: var(--danger-500);
  transition: transform var(--motion-fast) var(--ease-out);
}

.conflict-block__chevron.is-open {
  transform: rotate(180deg);
}

.conflict-block__detail {
  padding: 0 var(--space-4) var(--space-3) calc(var(--space-4) + 26px);
}

.conflict-block__sides {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-2);
}

@media (min-width: 640px) {
  .conflict-block__sides {
    grid-template-columns: 1fr 1fr;
  }
}

.conflict-block__side {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  padding: var(--space-2) var(--space-3);
  min-width: 0;
}

.conflict-block__side-label {
  margin: 0 0 2px;
  font-size: var(--font-xs);
  font-weight: 600;
  color: var(--danger-500);
}

.conflict-block__side-title {
  margin: 0 0 var(--space-1);
  font-size: var(--font-xs);
  line-height: 1.5;
  color: var(--color-text);
  word-break: break-word;
}

.conflict-block__side-fallback {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-family: ui-monospace, monospace;
}

.conflict-block__note {
  margin: var(--space-2) 0 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
</style>
