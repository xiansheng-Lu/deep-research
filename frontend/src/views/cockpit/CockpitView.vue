<script setup lang="ts">
// 研究指挥舱（[前端详细设计 §11.3] M1 最简）：
// 问题标题与状态、阶段进度与 token 用量、六阶段纵向时间线（实时推进）、
// 实时断线重连提示、失败终态错误信息；succeeded 提供查看报告入口。
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRunStream, type StageRuntime } from '@/composables/useRunStream'
import { RESEARCH_STAGES } from '@/services/domain/stages'
import { runStatusLabel, runStatusVariant, stageLabel, tierLabel } from '@/services/i18n/zh-CN'
import { formatNumber } from '@/utils/format'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'

const route = useRoute()
const router = useRouter()
const projectId = String(route.params.projectId)
const runId = String(route.params.runId)

const { state, reload } = useRunStream(runId)

const run = computed(() => state.run)

// 时间线节点状态文案
const STAGE_STATUS_TEXT: Record<StageRuntime['status'], string> = {
  pending: '待执行',
  running: '进行中',
  done: '已完成',
  failed: '失败'
}

// 当前阶段序号（1-6）；终态成功视为 6/6
const currentStageNo = computed<number | null>(() => {
  const entity = run.value
  if (!entity) return null
  if (entity.status === 'succeeded') return RESEARCH_STAGES.length
  if (entity.current_stage) {
    const index = RESEARCH_STAGES.indexOf(entity.current_stage)
    return index === -1 ? null : index + 1
  }
  return null
})

// token 用量百分比（预算超支时条铺满即可）
const tokenPercent = computed<number>(() => {
  const entity = run.value
  if (!entity || entity.token_budget <= 0) return 0
  return Math.min(100, Math.round((entity.token_used / entity.token_budget) * 100))
})

const tasksHref = computed(() => `/projects/${encodeURIComponent(projectId)}/tasks`)
const reportHref = computed(
  () => `/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/report`
)

function backToTasks(): void {
  router.push(tasksHref.value)
}

function openReport(): void {
  router.push(reportHref.value)
}
</script>

<template>
  <section class="cockpit-view">
    <UiErrorState
      v-if="state.notFound"
      title="资源不存在或无权访问"
      :show-retry="false"
      show-back
      @back="backToTasks"
    />

    <UiErrorState
      v-else-if="state.error && !run"
      :error="state.error"
      @retry="reload"
    />

    <template v-else>
      <RouterLink
        :to="tasksHref"
        class="cockpit-view__back"
      >
        返回任务列表
      </RouterLink>

      <UiSkeleton
        v-if="state.loading && !run"
        width="100%"
        height="320px"
        radius="md"
      />

      <template v-else-if="run">
        <header class="cockpit-head">
          <h1
            class="cockpit-head__question"
            :title="run.question"
          >
            {{ run.question }}
          </h1>
          <div class="cockpit-head__meta">
            <UiBadge :variant="runStatusVariant(run.status)">
              {{ runStatusLabel(run.status) }}
            </UiBadge>
            <UiBadge variant="brand">
              {{ tierLabel(run.tier) }}
            </UiBadge>
            <span class="cockpit-head__stage-no">
              阶段 {{ currentStageNo ?? '-' }}/{{ RESEARCH_STAGES.length }}
            </span>
            <span class="cockpit-head__tokens">
              Token {{ formatNumber(run.token_used) }} / {{ formatNumber(run.token_budget) }}
            </span>
          </div>
          <div
            class="token-bar"
            role="img"
            :aria-label="`Token 用量 ${tokenPercent}%`"
          >
            <span
              class="token-bar__fill"
              :style="{ width: `${tokenPercent}%` }"
            />
          </div>
        </header>

        <p
          v-if="state.channelState === 'connecting'"
          class="cockpit-view__notice cockpit-view__notice--connecting"
          role="status"
        >
          实时连接建立中，阶段状态通过同步接口持续跟踪…
        </p>

        <p
          v-else-if="state.channelState === 'retrying'"
          class="cockpit-view__notice"
          role="status"
        >
          实时连接中断，正在自动重连…
        </p>

        <div
          v-if="run.status === 'failed'"
          class="cockpit-error"
          role="alert"
        >
          <p class="cockpit-error__title">
            研究执行失败
          </p>
          <p
            v-if="run.error_code"
            class="cockpit-error__code"
          >
            {{ run.error_code }}
          </p>
          <p
            v-if="run.error_message"
            class="cockpit-error__message"
          >
            {{ run.error_message }}
          </p>
        </div>

        <div
          v-if="run.status === 'paused'"
          class="cockpit-paused"
          role="status"
        >
          <p class="cockpit-paused__title">
            研究已暂停：等待澄清确认
          </p>
          <p class="cockpit-paused__message">
            系统在「{{ run.current_stage ? stageLabel(run.current_stage) : '澄清界定' }}」阶段需要补充信息后才能继续。M1 暂未提供页面恢复入口，可重新发起研究并把问题描述得更明确。
          </p>
        </div>

        <ol class="stage-timeline">
          <li
            v-for="stage in state.stages"
            :key="stage.name"
            class="stage-timeline__item"
            :class="`is-${stage.status}`"
          >
            <span
              class="stage-timeline__dot"
              aria-hidden="true"
            />
            <div class="stage-timeline__body">
              <p class="stage-timeline__name">
                {{ stageLabel(stage.name) }}
              </p>
              <p class="stage-timeline__meta">
                <span>{{ STAGE_STATUS_TEXT[stage.status] }}</span>
                <span
                  v-if="stage.attempt !== null && stage.status !== 'pending'"
                  class="stage-timeline__attempt"
                >
                  · 第 {{ stage.attempt }} 次尝试
                </span>
              </p>
            </div>
          </li>
        </ol>

        <div
          v-if="run.status === 'succeeded'"
          class="cockpit-actions"
        >
          <UiButton
            variant="primary"
            @click="openReport"
          >
            查看报告
          </UiButton>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.cockpit-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: var(--space-8);
}

.cockpit-view__back {
  display: inline-block;
  margin-bottom: var(--space-4);
  font-size: var(--font-sm);
  color: var(--neutral-600);
}

.cockpit-view__back:hover {
  color: var(--brand-700);
}

.cockpit-head {
  margin-bottom: var(--space-6);
}

.cockpit-head__question {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  line-height: 1.4;
  color: var(--color-text-strong);
  margin-bottom: var(--space-3);
}

.cockpit-head__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.cockpit-head__stage-no,
.cockpit-head__tokens {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.token-bar {
  height: 6px;
  border-radius: 999px;
  background: var(--neutral-100);
  overflow: hidden;
}

.token-bar__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--brand-500);
  transition: width 0.4s ease;
}

.cockpit-view__notice {
  margin: 0 0 var(--space-4);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-sm);
  background: var(--warning-50);
  color: var(--warning-500);
  font-size: var(--font-sm);
}

.cockpit-view__notice--connecting {
  background: var(--brand-50);
  color: var(--brand-700);
}

.cockpit-error {
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--danger-500);
  border-radius: var(--radius-md);
  background: var(--danger-50);
}

.cockpit-error__title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--danger-500);
  margin-bottom: var(--space-2);
}

.cockpit-error__code {
  font-size: var(--font-xs);
  font-family: ui-monospace, monospace;
  color: var(--danger-500);
  margin-bottom: var(--space-1);
}

.cockpit-error__message {
  font-size: var(--font-sm);
  color: var(--color-text);
}

.cockpit-paused {
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--warning-500);
  border-radius: var(--radius-md);
  background: var(--warning-50);
}

.cockpit-paused__title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--warning-500);
  margin-bottom: var(--space-2);
}

.cockpit-paused__message {
  font-size: var(--font-sm);
  color: var(--color-text);
}

.stage-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}

.stage-timeline__item {
  position: relative;
  display: flex;
  gap: var(--space-4);
  padding-bottom: var(--space-6);
}

.stage-timeline__item::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 18px;
  bottom: -2px;
  width: 2px;
  background: var(--neutral-200);
}

.stage-timeline__item:last-child {
  padding-bottom: 0;
}

.stage-timeline__item:last-child::before {
  display: none;
}

.stage-timeline__dot {
  position: relative;
  z-index: 1;
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  margin-top: 3px;
  border-radius: 50%;
  border: 2px solid var(--neutral-400);
  background: var(--color-surface);
  box-sizing: border-box;
}

.stage-timeline__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stage-timeline__name {
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text-muted);
}

.stage-timeline__meta {
  display: flex;
  gap: var(--space-1);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.stage-timeline__item.is-done .stage-timeline__dot {
  border-color: var(--success-500);
  background: var(--success-500);
}

.stage-timeline__item.is-done .stage-timeline__name {
  color: var(--color-text);
}

.stage-timeline__item.is-running .stage-timeline__dot {
  border-color: var(--brand-500);
  background: var(--brand-500);
  animation: stage-pulse 1.6s ease-in-out infinite;
}

.stage-timeline__item.is-running .stage-timeline__name {
  color: var(--color-text-strong);
}

.stage-timeline__item.is-running .stage-timeline__meta {
  color: var(--brand-700);
}

.stage-timeline__item.is-failed .stage-timeline__dot {
  border-color: var(--danger-500);
  background: var(--danger-500);
}

.stage-timeline__item.is-failed .stage-timeline__name,
.stage-timeline__item.is-failed .stage-timeline__meta {
  color: var(--danger-500);
}

.stage-timeline__attempt {
  white-space: pre;
}

.cockpit-actions {
  margin-top: var(--space-6);
}

@keyframes stage-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}
</style>
