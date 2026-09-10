<script setup lang="ts">
// 报告阅读页（[前端详细设计 §11.4] M1 最简）：
// 先 GET run 判定可展示性：未结束给状态卡与返回入口，failed 给错误信息；
// succeeded 后 GET /runs/{id}/report，422（报告尚未生成）展示生成中态并 3 秒轮询；
// 正文经 marked 解析、DOMPurify 白名单净化后 v-html 渲染，链接统一新窗口打开。
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { getRun } from '@/services/api/runs'
import { getRunReport } from '@/services/api/reports'
import type { ReportResponse, RunResponse } from '@/services/api/types'
import type { ApiError } from '@/services/http/error'
import { runStatusLabel, stageLabel, tierLabel } from '@/services/i18n/zh-CN'
import { formatDateTime, formatNumber } from '@/utils/format'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'

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
const renderedHtml = ref('')

const bodyRef = ref<HTMLElement | null>(null)

const POLL_INTERVAL_MS = 3000
let pollTimer: number | undefined

const cockpitHref = `/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/cockpit`

const activeStageText = computed(() => {
  const entity = run.value
  if (!entity) return ''
  return entity.current_stage ? stageLabel(entity.current_stage) : '等待启动'
})

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
  if (phase.value !== 'generating') {
    reportError.value = null
    if (report.value === null) phase.value = 'loading-report'
  }
  try {
    const data = await getRunReport(runId)
    stopPolling()
    report.value = data
    const parsed = marked.parse(data.content_md, { async: false })
    renderedHtml.value = DOMPurify.sanitize(parsed)
    phase.value = 'ready'
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
  phase.value = 'loading-run'
  report.value = null
  renderedHtml.value = ''
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
    phase.value = apiError.status === 404 ? 'not-found' : 'run-error'
  }
}

function backToCockpit(): void {
  router.push(cockpitHref)
}

function backToProjects(): void {
  router.push('/projects')
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
      @retry="init"
    />

    <template v-else>
      <RouterLink
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
        show-back
        @retry="loadReport"
        @back="backToCockpit"
      />

      <template v-else-if="phase === 'ready' && run && report">
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
