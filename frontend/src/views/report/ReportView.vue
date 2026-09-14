<script setup lang="ts">
// 报告阅读页（[前端详细设计 §11.4] M1 markdown 轨 / M2 blocks 轨双轨）：
// 先 GET run 判定可展示性：未结束给状态卡，failed 给错误信息；
// succeeded 后 GET /runs/{id}/report，422（报告尚未生成）展示生成中态并 3 秒轮询。
// ready 后按终稿响应形态分流（WP-16 计划决策 1，客观阶段差异非旧版兼容）：
// - 含非空 blocks（demo_full；M2-7 数据点级溯源冻结后的目标形态）→ blocks 结构化轨道；
// - 不含 blocks（happy_path 与当前真链）→ marked + DOMPurify 的 markdown 轨道。
// M2-7 冻结、终稿全部结构化后，删除 markdown 终稿分支与 marked/DOMPurify 渲染。
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { getRun } from '@/services/api/runs'
import { getRunReport } from '@/services/api/reports'
import type { ReportResponse, RunResponse } from '@/services/api/types'
import type { ApiError } from '@/services/http/error'
import { runStatusLabel, stageLabel, tierLabel, zhCN } from '@/services/i18n/zh-CN'
import { formatDateTime, formatNumber, formatPublishedAt } from '@/utils/format'
import { useReportBlocks } from '@/composables/useReportBlocks'
// 本页已有表示渲染轨道的 ref track，埋点 track 以 trackEvent 别名导入
import { track as trackEvent } from '@/services/telemetry/telemetry'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'
import ReportChrome from '@/components/business/ReportChrome.vue'
import ReportBlockView from '@/components/business/ReportBlockView.vue'
import SourcePanel from '@/components/business/SourcePanel.vue'
import LimitationSummary from '@/components/business/LimitationSummary.vue'
import SourceBadge from '@/components/business/SourceBadge.vue'

const route = useRoute()
const router = useRouter()
const projectId = String(route.params.projectId)
const runId = String(route.params.runId)

// 页面阶段：run 初态判定 → 报告加载/生成中 → 可读
type Phase =
  | 'loading-run'
  | 'not-found'
  | 'run-error'
  | 'active'
  | 'failed'
  | 'loading-report'
  | 'generating'
  | 'report-error'
  | 'ready'

const phase = ref<Phase>('loading-run')
const run = ref<RunResponse | null>(null)
const report = ref<ReportResponse | null>(null)
const reportError = ref<ApiError | null>(null)
// run 初态判定失败（404 除外）：网络/5xx 时错误卡自动重试恢复
const runError = ref<ApiError | null>(null)
const renderedHtml = ref('')

// blocks 轨取数与交互状态（WP-16）
const reportBlocks = useReportBlocks(runId)
const track = ref<'markdown' | 'blocks'>('markdown')

const bodyRef = ref<HTMLElement | null>(null)

const POLL_INTERVAL_MS = 3000
let pollTimer: number | undefined

const cockpitHref = `/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/cockpit`

const activeStageText = computed(() => {
  const entity = run.value
  if (!entity) return ''
  return entity.current_stage ? stageLabel(entity.current_stage) : '等待启动'
})

// blocks 轨就绪时工具条内自带返回链接，其余阶段使用页面级返回链接
const showPageBack = computed(() => !(phase.value === 'ready' && track.value === 'blocks'))

function stopPolling(): void {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function startPolling(): void {
  stopPolling()
  pollTimer = window.setInterval(() => {
    void loadReport()
  }, POLL_INTERVAL_MS)
}

// 拉取报告；422 进入生成中轮询，其余错误就地展示
async function loadReport(): Promise<void> {
  // WP-17：report-error 自动重试期间保留错误卡倒计时，不回退 loading 态
  if (phase.value !== 'generating' && phase.value !== 'report-error') {
    reportError.value = null
    if (report.value === null) phase.value = 'loading-report'
  }
  try {
    const data = await getRunReport(runId)
    stopPolling()
    reportError.value = null
    report.value = data
    const parsed = marked.parse(data.content_md, { async: false })
    renderedHtml.value = DOMPurify.sanitize(parsed)
    // 结构化终稿增强：mock demo_full 返回 markdown+blocks 超集；
    // 真链 M2-7 前仅回 markdown（或溯源端点未就绪导致增强失败），均留在 markdown 轨
    await reportBlocks.load()
    track.value = reportBlocks.hasBlocks.value ? 'blocks' : 'markdown'
    phase.value = 'ready'
    // WP-18：终稿首次可读记一次报告浏览（组件每次挂载独立计为一次浏览），
    // 仅记轨道分类与是否含 blocks，不记报告正文
    trackEvent(
      'report.view',
      { render_track: track.value, has_blocks: reportBlocks.hasBlocks.value ? 1 : 0 },
      runId
    )
  } catch (err) {
    const apiError = err as ApiError
    if (apiError.status === 422) {
      // 研究已终态但报告尚未落盘：生成中态，轮询直至可读
      phase.value = 'generating'
      startPolling()
    } else {
      stopPolling()
      reportError.value = apiError
      phase.value = 'report-error'
    }
  }
}

// 入口：先判定 run 状态，再决定是否取报告
async function init(): Promise<void> {
  stopPolling()
  // WP-17：run-error 自动重试期间保留错误卡倒计时，不回退 loading 态
  if (phase.value !== 'run-error') {
    phase.value = 'loading-run'
    runError.value = null
  }
  report.value = null
  renderedHtml.value = ''
  track.value = 'markdown'
  try {
    const entity = await getRun(runId)
    run.value = entity
    if (entity.status === 'failed') {
      phase.value = 'failed'
      return
    }
    if (entity.status === 'succeeded') {
      await loadReport()
      return
    }
    // pending / running / paused / cancelled：报告尚不可得，展示进行态
    phase.value = 'active'
  } catch (err) {
    const apiError = err as ApiError
    if (apiError.status === 404) {
      phase.value = 'not-found'
      return
    }
    runError.value = apiError
    phase.value = 'run-error'
  }
}

function backToCockpit(): void {
  router.push(cockpitHref)
}

function backToProjects(): void {
  router.push('/projects')
}

// 溯源抽屉只能由内部 mask/Esc/关闭键请求关闭，打开入口只有正文角标
function onSourcePanelOpenUpdate(value: boolean): void {
  if (!value) reportBlocks.closeSourcePanel()
}

function setDisputesOnly(value: boolean): void {
  reportBlocks.disputesOnly.value = value
}

// 正文渲染后统一为链接补新窗口与安全 opener 属性
watch(
  renderedHtml,
  () => {
    void nextTick(() => {
      const container = bodyRef.value
      if (!container) return
      container.querySelectorAll('a[href]').forEach((anchor) => {
        anchor.setAttribute('target', '_blank')
        anchor.setAttribute('rel', 'noopener noreferrer')
      })
    })
  },
  { flush: 'post' }
)

onUnmounted(stopPolling)

void init()
</script>

<template>
  <section class="report-view">
    <UiErrorState
      v-if="phase === 'not-found'"
      title="资源不存在或无权访问"
      :show-retry="false"
      show-back
      @back="backToProjects"
    />

    <UiErrorState
      v-else-if="phase === 'run-error'"
      :error="runError"
      auto-retry
      @retry="init"
    />

    <template v-else>
      <RouterLink
        v-if="showPageBack"
        :to="cockpitHref"
        class="report-view__back"
      >
        返回指挥舱
      </RouterLink>

      <UiSkeleton
        v-if="phase === 'loading-run' || phase === 'loading-report'"
        width="100%"
        height="360px"
        radius="md"
      />

      <div
        v-else-if="phase === 'active' && run"
        class="report-state-card"
      >
        <h2 class="report-state-card__title">
          报告尚未生成
        </h2>
        <p class="report-state-card__hint">
          研究仍在进行中，当前阶段：{{ activeStageText }}（{{ runStatusLabel(run.status) }}）
        </p>
        <UiButton
          variant="primary"
          @click="backToCockpit"
        >
          返回指挥舱查看进度
        </UiButton>
      </div>

      <div
        v-else-if="phase === 'failed' && run"
        class="report-state-card report-state-card--error"
        role="alert"
      >
        <h2 class="report-state-card__title">
          研究执行失败，未生成报告
        </h2>
        <p
          v-if="run.error_code"
          class="report-state-card__code"
        >
          {{ run.error_code }}
        </p>
        <p
          v-if="run.error_message"
          class="report-state-card__hint"
        >
          {{ run.error_message }}
        </p>
        <UiButton
          variant="secondary"
          @click="backToCockpit"
        >
          返回指挥舱
        </UiButton>
      </div>

      <div
        v-else-if="phase === 'generating'"
        class="report-state-card"
      >
        <span
          class="report-state-card__pulse"
          aria-hidden="true"
        />
        <h2 class="report-state-card__title">
          报告生成中
        </h2>
        <p class="report-state-card__hint">
          研究已完成，正在整理最终报告，页面将自动刷新
        </p>
        <UiButton
          variant="secondary"
          @click="loadReport"
        >
          立即重新检查
        </UiButton>
      </div>

      <UiErrorState
        v-else-if="phase === 'report-error'"
        :error="reportError"
        auto-retry
        show-back
        @retry="loadReport"
        @back="backToCockpit"
      />

      <template v-else-if="phase === 'ready' && run && report">
        <!-- ─── blocks 结构化轨道（WP-16） ─── -->
        <template v-if="track === 'blocks'">
          <ReportChrome
            :back-href="cockpitHref"
            :question="run.question"
            :tier="run.tier"
            :created-at="report.created_at"
            :token-used="report.token_used"
            :disputes-only="reportBlocks.disputesOnly.value"
            @update:disputes-only="setDisputesOnly"
          />

          <!-- 窄屏目录：<1024px 折叠展示，≥1024px 由左侧粘性目录承接 -->
          <details class="report-outline-compact">
            <summary class="report-outline-compact__summary">
              {{ zhCN.report.outlineTitle }}
            </summary>
            <nav>
              <ol class="report-outline-compact__list">
                <li
                  v-for="(item, outlineIndex) in reportBlocks.outline.value"
                  :key="item.id"
                >
                  <button
                    type="button"
                    class="report-outline-compact__item"
                    @click="reportBlocks.locateOutline(item.type, outlineIndex)"
                  >
                    {{ item.title }}
                  </button>
                </li>
              </ol>
            </nav>
          </details>

          <div class="report-blocks__layout">
            <aside class="report-outline">
              <h2 class="report-outline__title">
                {{ zhCN.report.outlineTitle }}
              </h2>
              <nav>
                <ol class="report-outline__list">
                  <li
                    v-for="(item, outlineIndex) in reportBlocks.outline.value"
                    :key="item.id"
                  >
                    <button
                      type="button"
                      class="report-outline__item"
                      @click="reportBlocks.locateOutline(item.type, outlineIndex)"
                    >
                      {{ item.title }}
                    </button>
                  </li>
                </ol>
              </nav>
            </aside>

            <div class="report-blocks__main">
              <div class="report-blocks__list">
                <ReportBlockView
                  v-for="(block, blockIndex) in reportBlocks.blocks.value"
                  :key="block.id ?? `block-${blockIndex}`"
                  v-bind="reportBlocks.disputeOf(block)"
                  :block="block"
                  :index="blockIndex"
                  :dimmed="reportBlocks.isBlockDimmed(block)"
                  :citation-by-marker="reportBlocks.citationByMarker.value"
                  @open-source="reportBlocks.openSource"
                />
              </div>

              <!-- 底部信源索引区（完整溯源索引，链接新窗口打开） -->
              <section class="report-citation-index">
                <h2 class="report-citation-index__title">
                  {{ zhCN.report.citationIndexTitle }}
                </h2>
                <ol class="report-citation-index__list">
                  <li
                    v-for="item in reportBlocks.sortedCitations.value"
                    :key="item.evidence_id"
                    class="report-citation-index__item"
                  >
                    <span
                      class="report-citation-index__marker"
                      aria-hidden="true"
                    >{{ item.marker }}</span>
                    <div class="report-citation-index__body">
                      <a
                        :href="item.url"
                        class="report-citation-index__name"
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
                      <p class="report-citation-index__snippet">
                        {{ item.snippet }}
                      </p>
                      <span
                        v-if="item.published_at"
                        class="report-citation-index__published"
                      >
                        {{ zhCN.report.publishedAt }}：{{ formatPublishedAt(item.published_at) }}
                      </span>
                    </div>
                  </li>
                </ol>
              </section>

              <LimitationSummary :blocks="reportBlocks.limitationBlocks.value" />
            </div>
          </div>

          <SourcePanel
            :open="reportBlocks.sourcePanelOpen.value"
            :citations="reportBlocks.sortedCitations.value"
            :active-evidence-id="reportBlocks.activeEvidenceId.value"
            @update:open="onSourcePanelOpenUpdate"
          />
        </template>

        <!-- ─── markdown 轨道（M1 既有形态，M2-7 冻结后清退） ─── -->
        <template v-else>
          <header class="report-head">
            <h1
              class="report-head__question"
              :title="run.question"
            >
              {{ run.question }}
            </h1>
            <div class="report-head__meta">
              <UiBadge variant="brand">
                {{ tierLabel(run.tier) }}
              </UiBadge>
              <span>生成时间：{{ formatDateTime(report.created_at) }}</span>
              <span>Token 用量：{{ formatNumber(report.token_used) }}</span>
            </div>
          </header>

          <!-- eslint-disable vue/no-v-html -- 正文为 marked 解析后经 DOMPurify 白名单净化的可信 HTML -->
          <article
            ref="bodyRef"
            class="report-body"
            v-html="renderedHtml"
          />
          <!-- eslint-enable vue/no-v-html -->
        </template>
      </template>
    </template>
  </section>
</template>

<style scoped>
.report-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: var(--space-8);
}

.report-view__back {
  display: inline-block;
  margin-bottom: var(--space-4);
  font-size: var(--font-sm);
  color: var(--neutral-600);
}

.report-view__back:hover {
  color: var(--brand-700);
}

.report-state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  text-align: center;
  padding: var(--space-12) var(--space-6);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.report-state-card--error {
  border-color: var(--danger-500);
  background: var(--danger-50);
}

.report-state-card__title {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.report-state-card__hint {
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  max-width: 420px;
}

.report-state-card__code {
  font-size: var(--font-xs);
  font-family: ui-monospace, monospace;
  color: var(--danger-500);
}

.report-state-card__pulse {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--brand-500);
  animation: report-pulse 1.6s ease-in-out infinite;
}

/* ─── blocks 轨道布局 ─── */

.report-outline-compact {
  margin-bottom: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  padding: var(--space-2) var(--space-4);
}

.report-outline-compact__summary {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-strong);
  cursor: pointer;
}

.report-outline-compact__list,
.report-outline__list {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.report-outline-compact__item,
.report-outline__item {
  border: none;
  background: transparent;
  padding: var(--space-1) 0;
  text-align: left;
  font-size: var(--font-xs);
  line-height: 1.5;
  color: var(--color-text-muted);
  cursor: pointer;
}

.report-outline-compact__item:hover,
.report-outline__item:hover {
  color: var(--brand-700);
}

.report-outline {
  display: none;
}

@media (min-width: 1024px) {
  .report-outline-compact {
    display: none;
  }

  .report-blocks__layout {
    display: grid;
    grid-template-columns: 168px minmax(0, 1fr);
    gap: var(--space-8);
    align-items: start;
  }

  .report-outline {
    display: block;
    position: sticky;
    top: var(--space-8);
  }
}

.report-outline__title {
  margin: 0 0 var(--space-3);
  font-size: var(--font-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
}

.report-blocks__main {
  min-width: 0;
}

/* 底部信源索引区 */
.report-citation-index {
  margin-top: var(--space-8);
}

.report-citation-index__title {
  margin: 0 0 var(--space-4);
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.report-citation-index__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.report-citation-index__item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.report-citation-index__marker {
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

.report-citation-index__body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.report-citation-index__name {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--brand-700);
  word-break: break-word;
}

.report-citation-index__snippet {
  margin: 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text);
}

.report-citation-index__published {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

/* ─── markdown 轨道（M1 既有样式） ─── */

.report-head {
  margin-bottom: var(--space-8);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.report-head__question {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  line-height: 1.4;
  color: var(--color-text-strong);
  margin-bottom: var(--space-3);
}

.report-head__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-4);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

/* Markdown 排版（内容经 DOMPurify 白名单净化；v-html 需 :deep 穿透） */
.report-body {
  color: var(--color-text);
  font-size: var(--font-sm);
  line-height: 1.8;
}

.report-body :deep(h1),
.report-body :deep(h2),
.report-body :deep(h3),
.report-body :deep(h4) {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  color: var(--color-text-strong);
  line-height: 1.4;
}

.report-body :deep(h1) {
  font-size: var(--font-lg);
  margin: var(--space-8) 0 var(--space-4);
}

.report-body :deep(h2) {
  font-size: var(--font-base);
  margin: var(--space-8) 0 var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.report-body :deep(h3) {
  font-size: var(--font-sm);
  margin: var(--space-6) 0 var(--space-2);
}

.report-body :deep(p) {
  margin: 0 0 var(--space-4);
}

.report-body :deep(ul),
.report-body :deep(ol) {
  margin: 0 0 var(--space-4);
  padding-left: var(--space-6);
}

.report-body :deep(li) {
  margin-bottom: var(--space-2);
}

.report-body :deep(li::marker) {
  color: var(--brand-700);
}

.report-body :deep(blockquote) {
  margin: 0 0 var(--space-4);
  padding: var(--space-2) var(--space-4);
  border-left: 3px solid var(--brand-500);
  background: var(--brand-50);
  color: var(--neutral-600);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.report-body :deep(blockquote p:last-child) {
  margin-bottom: 0;
}

.report-body :deep(a) {
  color: var(--brand-700);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.report-body :deep(strong) {
  color: var(--color-text-strong);
  font-weight: 600;
}

.report-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.9em;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--neutral-100);
}

.report-body :deep(pre) {
  margin: 0 0 var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--neutral-100);
  overflow-x: auto;
}

.report-body :deep(pre code) {
  padding: 0;
  background: transparent;
}

.report-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 var(--space-4);
  font-size: var(--font-xs);
}

.report-body :deep(th),
.report-body :deep(td) {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  text-align: left;
}

.report-body :deep(th) {
  background: var(--neutral-50);
  color: var(--color-text-strong);
  font-weight: 600;
}

.report-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: var(--space-8) 0;
}

@keyframes report-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}
</style>
