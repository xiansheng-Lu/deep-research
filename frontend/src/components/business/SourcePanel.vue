<script setup lang="ts">
// 溯源抽屉（[前端详细设计 §10.3] click 分支 / §11.4 右侧覆盖）：
// 完整信源索引列表（角标/标题/域名/SourceBadge/发布时间/原文片段/URL 外链），
// 打开或切换证据时滚动定位并高亮当前项。只吃 citations 索引，不依赖证据池（计划决策 4）。
import { nextTick, watch } from 'vue'
import UiDrawer from '@/components/ui/overlay/UiDrawer.vue'
import UiEmpty from '@/components/ui/feedback/UiEmpty.vue'
import SourceBadge from '@/components/business/SourceBadge.vue'
import { zhCN } from '@/services/i18n/zh-CN'
import { formatPublishedAt } from '@/utils/format'
import type { ReportCitationItem } from '@/services/api/types'

const props = defineProps<{
  open: boolean
  citations: ReportCitationItem[]
  activeEvidenceId: string | null
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
}>()

function scrollToActive(behavior: ScrollBehavior): void {
  if (!props.activeEvidenceId) return
  const el = document.getElementById(`source-item-${props.activeEvidenceId}`)
  el?.scrollIntoView({ behavior, block: 'center' })
}

// 抽屉打开后定位（等待 Teleport 与过渡内节点挂载）
watch(
  () => props.open,
  (open) => {
    if (open) void nextTick(() => scrollToActive('auto'))
  }
)

// 抽屉已开时切换角标：平滑滚动到新证据
watch(
  () => props.activeEvidenceId,
  () => {
    if (props.open) void nextTick(() => scrollToActive('smooth'))
  }
)
</script>

<template>
  <UiDrawer
    :model-value="open"
    size="md"
    :title="zhCN.report.sourcePanelTitle"
    @update:model-value="emit('update:open', $event)"
  >
    <p class="source-panel__hint">
      {{ zhCN.report.sourcePanelHint }}
    </p>

    <UiEmpty
      v-if="citations.length === 0"
      :title="zhCN.report.sourceEmpty"
    />

    <ol
      v-else
      class="source-panel__list"
    >
      <li
        v-for="item in citations"
        :id="`source-item-${item.evidence_id}`"
        :key="item.evidence_id"
        class="source-panel__item"
        :class="{ 'is-active': item.evidence_id === activeEvidenceId }"
      >
        <span
          class="source-panel__marker"
          aria-hidden="true"
        >{{ item.marker }}</span>
        <div class="source-panel__body">
          <a
            :href="item.url"
            class="source-panel__title"
            target="_blank"
            rel="noopener noreferrer"
            :title="item.title"
          >
            {{ item.title }}
          </a>
          <SourceBadge
            v-if="item.source_type && item.credibility"
            :domain="item.domain ?? ''"
            :source-type="item.source_type"
            :source-level="item.source_level"
            :credibility="item.credibility"
          />
          <p
            v-else-if="item.domain"
            class="source-panel__domain"
          >
            {{ item.domain }}
          </p>
          <p class="source-panel__snippet">
            {{ item.snippet }}
          </p>
          <div class="source-panel__meta">
            <span
              v-if="item.published_at"
              class="source-panel__published"
            >
              {{ zhCN.report.publishedAt }}：{{ formatPublishedAt(item.published_at) }}
            </span>
            <a
              :href="item.url"
              class="source-panel__link"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ zhCN.report.visitSource }}
            </a>
          </div>
        </div>
      </li>
    </ol>
  </UiDrawer>
</template>

<style scoped>
.source-panel__hint {
  margin: 0 0 var(--space-4);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.source-panel__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.source-panel__item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  transition:
    border-color var(--motion-fast) var(--ease-out),
    background-color var(--motion-fast) var(--ease-out);
}

.source-panel__item.is-active {
  border-color: var(--brand-500);
  background: var(--brand-50);
}

.source-panel__marker {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 20px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: var(--font-xs);
}

.source-panel__body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.source-panel__title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--brand-700);
  word-break: break-word;
}

.source-panel__domain {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.source-panel__snippet {
  margin: 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text);
}

.source-panel__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-top: var(--space-1);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.source-panel__link {
  flex: 0 0 auto;
  color: var(--brand-700);
}
</style>
