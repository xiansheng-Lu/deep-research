<script setup lang="ts">
// 研究指挥舱 · 实时看板（[前端详细设计 §11.3 M2]，WP-14）
// 双列布局：左=StageTimeline + SubQuestionPlan（只读 m/n）+ 冲突提示列表；
// 右=当前阶段描述 + 证据流（REST 分页与实时增量双源、全文懒加载）+ CostMeter。
// 顶栏提供暂停（模态确认，软暂停受控通道）；取消按钮待后端 M2-5 能力交付后再开放，不预造入口。
// 澄清回答/继续/追问/剔除等介入入口在 WP-15。
import { computed, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRunStream } from '@/composables/useRunStream'
import { useEvidenceList } from '@/composables/useEvidenceList'
import { RESEARCH_STAGES, STAGE_DESCRIPTIONS } from '@/services/domain/stages'
import { runStatusLabel, runStatusVariant, stageLabel, tierLabel } from '@/services/i18n/zh-CN'
import { formatDuration } from '@/utils/format'
import type { ApiError } from '@/services/http/error'
import type { EvidenceResponse } from '@/services/api/types'
import { toast } from '@/services/toast/toast'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiDialog from '@/components/ui/overlay/UiDialog.vue'
import UiEmpty from '@/components/ui/feedback/UiEmpty.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'
import StageTimeline from '@/components/business/StageTimeline.vue'
import SubQuestionPlan from '@/components/business/SubQuestionPlan.vue'
import EvidenceCard from '@/components/business/EvidenceCard.vue'
import CostMeter from '@/components/business/CostMeter.vue'
import ConflictBlock from '@/components/business/ConflictBlock.vue'

const route = useRoute()
const router = useRouter()
const projectId = String(route.params.projectId)
const runId = String(route.params.runId)

const { state, derived, reload, actions } = useRunStream(runId)
const evidenceList = useEvidenceList(runId, { pageSize: 10 })

const run = computed(() => state.run)
const sqStats = derived.subQuestions
const conflictStats = derived.conflicts

// ─── 顶栏派生：阶段序号 / 已用时长 ───

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

// 右列「当前阶段」：running 阶段优先，退回 run.current_stage
const currentStage = computed(() => {
  const running = state.stages.find((item) => item.status === 'running')
  if (running) return running.name
  return run.value?.current_stage ?? null
})

const failedStage = computed(() => state.stages.find((item) => item.status === 'failed') ?? null)

// 已用时长：1s 心跳驱动；终态用 finished_at 截停
const nowTs = ref(Date.now())
const tickTimer = window.setInterval(() => {
  nowTs.value = Date.now()
}, 1000)
onUnmounted(() => window.clearInterval(tickTimer))

const elapsedSeconds = computed<number | null>(() => {
  const entity = run.value
  if (!entity?.started_at) return null
  const start = new Date(entity.started_at).getTime()
  if (Number.isNaN(start)) return null
  const end = entity.finished_at ? new Date(entity.finished_at).getTime() : nowTs.value
  return Math.max(0, Math.floor((end - start) / 1000))
})

// ─── 证据流：展开态与全文懒加载编排 ───

const expandedEvidenceIds = ref<Set<string>>(new Set())

function toggleEvidence(evidenceId: string): void {
  const next = new Set(expandedEvidenceIds.value)
  if (next.has(evidenceId)) {
    next.delete(evidenceId)
  } else {
    next.add(evidenceId)
    // 内容经 useEvidenceList 缓存，重复调用不会发第二次请求
    void evidenceList.loadContent(evidenceId)
  }
  expandedEvidenceIds.value = next
}

// 子问题序号标签（证据归属反查）
const sqLabelMap = computed(() => {
  const map = new Map<string, string>()
  state.subQuestions.forEach((item, index) => map.set(item.id, `子问题 ${index + 1}`))
  return map
})

// 冲突双方证据反查：实时态与分页列表取并集
const evidenceMap = computed(() => {
  const map = new Map<string, EvidenceResponse>()
  for (const item of state.evidence) map.set(item.id, item)
  for (const item of evidenceList.items.value) map.set(item.id, item)
  return map
})

// 成本卡滞后口径：仅 retrying 标「可能滞后」；connecting 首帧前展示的是 REST 快照
const costStale = computed(() => state.channelState === 'retrying')

// 澄清挂起态：实时 interrupt 帧优先；页面在 paused 后刷新（无 WS）时按阶段口径兜底推导
const awaitingClarification = computed(
  () =>
    state.interrupt !== null ||
    (run.value?.status === 'paused' && run.value?.current_stage === 'clarify')
)
const pausedStageName = computed(
  () => state.interrupt?.stage ?? run.value?.current_stage ?? 'clarify'
)

// ─── 暂停（软暂停，模态确认）───

const pauseDialogOpen = ref(false)
const pauseSubmitting = ref(false)

function openPauseDialog(): void {
  pauseDialogOpen.value = true
}

async function confirmPause(): Promise<void> {
  pauseSubmitting.value = true
  try {
    await actions.pause()
    pauseDialogOpen.value = false
    toast.success('研究已暂停，系统将在当前安全点停止推进')
    // 立即重取快照收敛 paused 态（暂停后实时通道关闭）
    await reload()
  } catch (err) {
    const apiError = err as ApiError
    toast.danger(apiError?.title || '暂停失败', {
      description: apiError?.detail || apiError?.code || undefined
    })
  } finally {
    pauseSubmitting.value = false
  }
}

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
        height="360px"
        radius="md"
      />

      <template v-else-if="run">
        <!-- 顶栏：标题 / 状态汇总 / 暂停与报告入口 -->
        <header class="cockpit-head">
          <div class="cockpit-head__main">
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
              <span class="cockpit-head__metric">阶段 {{ currentStageNo ?? '-' }}/{{ RESEARCH_STAGES.length }}</span>
              <span class="cockpit-head__metric">子问题 {{ sqStats.completed }}/{{ sqStats.total }}</span>
              <span
                v-if="elapsedSeconds !== null"
                class="cockpit-head__metric"
              >已用时长 {{ formatDuration(elapsedSeconds) }}</span>
            </div>
          </div>
          <div class="cockpit-head__actions">
            <UiButton
              v-if="run.status === 'running'"
              variant="secondary"
              size="sm"
              @click="openPauseDialog"
            >
              暂停
            </UiButton>
            <UiButton
              v-if="run.status === 'succeeded'"
              size="sm"
              @click="openReport"
            >
              查看报告
            </UiButton>
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
          实时连接中断，正在自动重连，当前显示为最近一次同步数据…
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
          <template v-if="awaitingClarification">
            <p class="cockpit-paused__title">
              研究已暂停：等待澄清确认
            </p>
            <p class="cockpit-paused__message">
              系统在「{{ stageLabel(pausedStageName) }}」阶段需要补充信息后才能继续。
            </p>
          </template>
          <template v-else>
            <p class="cockpit-paused__title">
              研究已暂停
            </p>
            <p class="cockpit-paused__message">
              系统已在安全点停止推进，已采集的证据与进度均已保留，实时事件暂停推送。
            </p>
          </template>
        </div>

        <!-- 双列看板 -->
        <div class="cockpit-grid">
          <!-- 左列：阶段时间线 + 子问题计划 + 冲突提示 -->
          <div class="cockpit-grid__left">
            <UiCard class="cockpit-panel">
              <h2 class="cockpit-panel__title">
                研究阶段
              </h2>
              <StageTimeline :stages="state.stages" />
            </UiCard>

            <UiCard class="cockpit-panel">
              <SubQuestionPlan :sub-questions="state.subQuestions" />
            </UiCard>

            <UiCard class="cockpit-panel">
              <header class="cockpit-panel__head">
                <h2 class="cockpit-panel__title">
                  冲突提示
                </h2>
                <UiBadge
                  v-if="conflictStats.unresolved > 0"
                  variant="danger"
                >
                  {{ conflictStats.unresolved }} 条待关注
                </UiBadge>
              </header>
              <p
                v-if="state.conflicts.length === 0"
                class="cockpit-panel__empty"
              >
                暂无冲突，审校阶段发现来源矛盾时将在此提示
              </p>
              <div
                v-else
                class="cockpit-conflicts"
              >
                <ConflictBlock
                  v-for="conflict in state.conflicts"
                  :key="conflict.id"
                  :conflict="conflict"
                  :evidence-a="evidenceMap.get(conflict.evidence_a_id) ?? null"
                  :evidence-b="evidenceMap.get(conflict.evidence_b_id) ?? null"
                />
              </div>
            </UiCard>
          </div>

          <!-- 右列：当前阶段 + 成本卡 + 证据流 -->
          <div class="cockpit-grid__right">
            <UiCard class="cockpit-panel">
              <h2 class="cockpit-panel__title">
                当前阶段
              </h2>
              <template v-if="run.status === 'succeeded'">
                <p class="cockpit-stage__name">
                  全部阶段已完成
                </p>
                <p class="cockpit-stage__desc">
                  六阶段流水线执行完毕，可查看带溯源角标的结构化研究报告。
                </p>
              </template>
              <template v-else-if="currentStage">
                <p class="cockpit-stage__name">
                  {{ stageLabel(currentStage) }}
                </p>
                <p class="cockpit-stage__desc">
                  {{ STAGE_DESCRIPTIONS[currentStage] }}
                </p>
              </template>
              <template v-else>
                <p class="cockpit-stage__name">
                  等待启动
                </p>
                <p class="cockpit-stage__desc">
                  研究运行创建后将按六阶段顺序推进。
                </p>
              </template>
              <p
                v-if="failedStage?.errorMessage"
                class="cockpit-stage__error"
              >
                {{ failedStage.errorMessage }}
              </p>
            </UiCard>

            <UiCard class="cockpit-panel">
              <CostMeter
                :cost="state.cost"
                :stale="costStale"
              />
            </UiCard>

            <UiCard class="cockpit-panel">
              <header class="cockpit-panel__head">
                <h2 class="cockpit-panel__title">
                  证据流
                </h2>
                <UiBadge variant="neutral">
                  {{ evidenceList.total.value }}
                </UiBadge>
              </header>

              <UiSkeleton
                v-if="evidenceList.loading.value && evidenceList.items.value.length === 0"
                width="100%"
                height="52px"
                :count="3"
              />

              <UiEmpty
                v-else-if="evidenceList.items.value.length === 0"
                title="证据尚未产生"
                hint="进入「证据检索」阶段后，新采集的证据会实时出现在这里"
              />

              <template v-else>
                <ul class="cockpit-evidence">
                  <li
                    v-for="evidence in evidenceList.items.value"
                    :key="evidence.id"
                  >
                    <EvidenceCard
                      :evidence="evidence"
                      :expanded="expandedEvidenceIds.has(evidence.id)"
                      :content="evidenceList.contents.value[evidence.id] ?? null"
                      :content-loading="evidenceList.isContentLoading(evidence.id)"
                      :content-error="evidenceList.contentError(evidence.id)"
                      :sq-label="sqLabelMap.get(evidence.sub_question_id) ?? null"
                      @toggle="toggleEvidence"
                    />
                  </li>
                </ul>

                <p
                  v-if="evidenceList.error.value"
                  class="cockpit-evidence__error"
                >
                  历史证据加载失败，实时证据不受影响
                </p>

                <div
                  v-if="evidenceList.hasMore.value"
                  class="cockpit-evidence__more"
                >
                  <UiButton
                    variant="secondary"
                    size="sm"
                    :loading="evidenceList.loadingMore.value"
                    @click="evidenceList.loadMore()"
                  >
                    加载更早证据
                  </UiButton>
                </div>
              </template>
            </UiCard>
          </div>
        </div>
      </template>
    </template>

    <!-- 暂停确认模态 -->
    <UiDialog
      v-model="pauseDialogOpen"
      size="sm"
      title="暂停研究"
      :close-on-mask="!pauseSubmitting"
      :close-on-esc="!pauseSubmitting"
    >
      <p class="cockpit-pause-dialog__text">
        暂停后系统将在当前安全点停止推进，已采集的证据与子问题进度都会保留，实时事件暂停推送。
      </p>
      <p class="cockpit-pause-dialog__text">
        确认暂停本次研究吗？
      </p>
      <template #footer>
        <UiButton
          variant="secondary"
          size="sm"
          :disabled="pauseSubmitting"
          @click="pauseDialogOpen = false"
        >
          再想想
        </UiButton>
        <UiButton
          variant="danger"
          size="sm"
          :loading="pauseSubmitting"
          @click="confirmPause"
        >
          确认暂停
        </UiButton>
      </template>
    </UiDialog>
  </section>
</template>

<style scoped>
.cockpit-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 1200px;
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
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.cockpit-head__main {
  min-width: 0;
}

.cockpit-head__question {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  line-height: 1.4;
  color: var(--color-text-strong);
  margin: 0 0 var(--space-3);
}

.cockpit-head__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.cockpit-head__metric {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.cockpit-head__actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
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
  margin-bottom: var(--space-4);
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
  margin: 0;
}

.cockpit-paused {
  margin-bottom: var(--space-4);
  padding: var(--space-4);
  border: 1px solid var(--warning-500);
  border-radius: var(--radius-md);
  background: var(--warning-50);
}

.cockpit-paused__title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--warning-500);
  margin: 0 0 var(--space-2);
}

.cockpit-paused__message {
  font-size: var(--font-sm);
  color: var(--color-text);
  margin: 0;
}

.cockpit-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-4);
}

@media (min-width: 1024px) {
  .cockpit-grid {
    grid-template-columns: 380px minmax(0, 1fr);
    align-items: start;
  }
}

.cockpit-grid__left,
.cockpit-grid__right {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.cockpit-panel {
  padding: var(--space-4);
}

.cockpit-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.cockpit-panel__title {
  margin: 0 0 var(--space-4);
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.cockpit-panel__head .cockpit-panel__title {
  margin-bottom: 0;
}

.cockpit-panel__empty {
  margin: 0;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}

.cockpit-conflicts {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cockpit-stage__name {
  margin: 0 0 var(--space-2);
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-strong);
}

.cockpit-stage__desc {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.6;
  color: var(--color-text);
}

.cockpit-stage__error {
  margin: var(--space-2) 0 0;
  font-size: var(--font-xs);
  color: var(--danger-500);
}

.cockpit-evidence {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.cockpit-evidence__error {
  margin: var(--space-2) 0 0;
  font-size: var(--font-xs);
  color: var(--warning-500);
}

.cockpit-evidence__more {
  margin-top: var(--space-3);
  text-align: center;
}

.cockpit-pause-dialog__text {
  margin: 0 0 var(--space-3);
  font-size: var(--font-sm);
  line-height: 1.6;
  color: var(--color-text);
}

.cockpit-pause-dialog__text:last-child {
  margin-bottom: 0;
}
</style>
