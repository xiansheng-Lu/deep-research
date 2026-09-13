<script setup lang="ts">
// 报告行内溯源角标 [n]（[前端详细设计 §10.3] 交互状态机）：
// - hover/focus 停留 200ms 弹出迷你来源卡（标题/域名/SourceBadge/一行原文）；
// - click 或键盘 Enter/Space 打开右侧 SourcePanel 并定位该证据；
// - 方向键在同页可交互角标间移动焦点；
// - 筛选「只看分歧」激活时，未命中 block 的角标灰化且不可点（保留上下文不删除）；
// - 溯源索引未就绪时渲染为不可点纯文本，不阻塞正文首帧（计划决策 8）。
import { computed, onBeforeUnmount, ref } from 'vue'
import UiPopover from '@/components/ui/overlay/UiPopover.vue'
import SourceBadge from '@/components/business/SourceBadge.vue'
import { zhCN } from '@/services/i18n/zh-CN'
import type { ReportCitationItem } from '@/services/api/types'

const props = withDefaults(
  defineProps<{
    marker: string
    // 索引就绪且命中时为引用项；null 时渲染纯文本角标
    citation: ReportCitationItem | null
    // 筛选灰化态（详设 §10.3 第四态）
    dimmed?: boolean
  }>(),
  { dimmed: false }
)

const emit = defineEmits<{
  (e: 'open', evidenceId: string): void
}>()

const miniOpen = ref(false)
const buttonRef = ref<HTMLButtonElement | null>(null)
let openTimer: number | undefined
let closeTimer: number | undefined

const markerNumberText = computed(() => props.marker.replace(/[[\]]/g, ''))
const interactive = computed(() => props.citation !== null && !props.dimmed)

function clearTimers(): void {
  if (openTimer !== undefined) window.clearTimeout(openTimer)
  if (closeTimer !== undefined) window.clearTimeout(closeTimer)
}

function scheduleOpen(): void {
  if (!props.citation || props.dimmed) return
  window.clearTimeout(closeTimer)
  openTimer = window.setTimeout(() => {
    miniOpen.value = true
  }, 200)
}

function scheduleClose(): void {
  window.clearTimeout(openTimer)
  closeTimer = window.setTimeout(() => {
    miniOpen.value = false
  }, 120)
}

function activate(): void {
  if (!interactive.value || !props.citation) return
  miniOpen.value = false
  // WP-18 report.citation.open 埋点在此接入（点击与键盘开抽屉的唯一入口）
  emit('open', props.citation.evidence_id)
}

// 在同页可交互角标间移动焦点（跳过纯文本与灰化角标）
function moveFocus(delta: number, event: KeyboardEvent): void {
  const all = Array.from(
    document.querySelectorAll<HTMLButtonElement>(
      'button[data-citation-marker="true"]:not([data-dimmed="true"])'
    )
  )
  const index = all.findIndex((el) => el === buttonRef.value)
  const target = all[index + delta]
  if (target) {
    event.preventDefault()
    target.focus()
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (!interactive.value) return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    activate()
  } else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    moveFocus(1, event)
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    moveFocus(-1, event)
  }
}

onBeforeUnmount(clearTimers)
</script>

<template>
  <UiPopover
    trigger="manual"
    placement="top"
    :model-value="miniOpen"
    :offset="6"
    :close-on-mask="false"
  >
    <template #trigger>
      <button
        v-if="citation"
        ref="buttonRef"
        type="button"
        class="citation-marker"
        :class="{ 'is-dimmed': dimmed }"
        data-citation-marker="true"
        :data-dimmed="dimmed ? 'true' : 'false'"
        :tabindex="dimmed ? -1 : 0"
        :aria-label="`${zhCN.report.viewSource} ${markerNumberText}`"
        :aria-disabled="dimmed || undefined"
        @mouseenter="scheduleOpen"
        @mouseleave="scheduleClose"
        @focus="scheduleOpen"
        @blur="scheduleClose"
        @click="activate"
        @keydown="onKeydown"
      >
        {{ marker }}
      </button>
      <span
        v-else
        class="citation-marker citation-marker--plain"
        data-citation-marker="false"
        aria-hidden="true"
      >{{ marker }}</span>
    </template>

    <div
      v-if="citation"
      class="citation-mini"
      @mouseenter="clearTimers"
      @mouseleave="scheduleClose"
    >
      <p
        class="citation-mini__title"
        :title="citation.title"
      >
        {{ citation.title }}
      </p>
      <SourceBadge
        v-if="citation.source_type && citation.credibility"
        :domain="citation.domain ?? ''"
        :source-type="citation.source_type"
        :source-level="citation.source_level"
        :credibility="citation.credibility"
      />
      <p
        v-else-if="citation.domain"
        class="citation-mini__domain"
      >
        {{ citation.domain }}
      </p>
      <p class="citation-mini__snippet">
        {{ citation.snippet }}
      </p>
    </div>
  </UiPopover>
</template>

<style scoped>
.citation-marker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 18px;
  padding: 0 4px;
  margin: 0 2px;
  border: 1px solid var(--brand-500);
  border-radius: var(--radius-sm);
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: var(--font-xs);
  line-height: 1;
  vertical-align: baseline;
  cursor: pointer;
  transition:
    background-color var(--motion-fast) var(--ease-out),
    color var(--motion-fast) var(--ease-out);
}

.citation-marker:hover,
.citation-marker:focus-visible {
  background: var(--brand-500);
  color: var(--neutral-0);
  outline: none;
}

.citation-marker.is-dimmed {
  border-color: var(--color-border);
  background: transparent;
  color: var(--neutral-400);
  cursor: default;
  opacity: 0.55;
}

.citation-marker--plain {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 18px;
  padding: 0 4px;
  margin: 0 2px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--neutral-400);
  font-size: var(--font-xs);
  line-height: 1;
  vertical-align: baseline;
}

.citation-mini {
  width: 260px;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.citation-mini__title {
  margin: 0;
  font-size: var(--font-xs);
  font-weight: 600;
  color: var(--color-text-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.citation-mini__domain {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.citation-mini__snippet {
  margin: 0;
  font-size: var(--font-xs);
  line-height: 1.5;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
