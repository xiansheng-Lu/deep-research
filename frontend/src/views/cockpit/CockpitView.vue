<script setup lang="ts">
// 研究指挥舱 · 实时看板（[前端详细设计 §11.3 M2]，WP-14/WP-15）
// 双列布局：左=StageTimeline + SubQuestionPlan（只读 m/n）+ 冲突提示列表；
// 右=当前阶段描述 + 证据流（REST 分页与实时增量双源、全文懒加载）+ CostMeter。
// WP-15 HITL 闭环：澄清卡（InterventionDrawer）、暂停/继续（超 15 分钟确认）、
// 阶段3 追加追问、证据剔除与可恢复列；全部经 useRunStream.actions 受控通道，不绕过编排层。
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRunStream } from '@/composables/useRunStream'
import { useEvidenceList } from '@/composables/useEvidenceList'
import { RESEARCH_STAGES, STAGE_DESCRIPTIONS } from '@/services/domain/stages'
import { runStatusLabel, runStatusVariant, stageLabel, tierLabel, zhCN } from '@/services/i18n/zh-CN'
import { formatDuration } from '@/utils/format'
import { getConflict } from '@/services/api/dashboard'
import type { ApiError } from '@/services/http/error'
import type { ConflictDetailResponse, EvidenceResponse } from '@/services/api/types'
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
import InterventionDrawer, {
  type InterventionMode
} from '@/components/business/InterventionDrawer.vue'
import { track } from '@/services/telemetry/telemetry'

// 软暂停后再次继续的确认时限：暂停超过此时长需用户二次确认（[前端详细设计 §11.3]）
const RESUME_CONFIRM_AFTER_MS = 15 * 60 * 1000

const route = useRoute()
const router = useRouter()
const projectId = String(route.params.projectId)
const runId = String(route.params.runId)

const { state, derived, reload, actions } = useRunStream(runId)
const evidenceList = useEvidenceList(runId, { pageSize: 10 })
// 可恢复列：含已剔除证据的独立列表，仅在出现剔除项时才拉取，避免常规流量放大
const excludedList = useEvidenceList(runId, {
  pageSize: 100,
  includeExcluded: true,
  immediate: false
})

const run = computed(() => state.run)
const sqStats = derived.subQuestions
const conflictStats = derived.conflicts

// WP-18：看板介入埋点。action 为封闭枚举（pause/resume/followup/exclude/clarify），
// 只记动作分类、成败与错误码，不记澄清答案/追问内容等敏感输入
type InterveneTrackAction = 'pause' | 'resume' | 'followup' | 'exclude' | 'clarify'
function trackIntervene(action: InterveneTrackAction, ok: boolean, err?: unknown): void {
  const apiError = err as ApiError | undefined
  track(
    'cockpit.intervene',
    {
      action,
      result: ok ? 'success' : 'fail',
      error_code: !ok && apiError?.code ? apiError.code : undefined
    },
    runId
  )
}

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

// 分歧详情懒加载（M2-2 交接单推荐路径）：展开冲突时按 id 拉一次，
// 内嵌 evidence_a/evidence_b 摘要不依赖证据池是否已加载（真实后端 M2-4 前证据端点可能缺）
const conflictDetails = ref<Record<string, ConflictDetailResponse>>({})
const conflictDetailLoading = ref<Set<string>>(new Set())

async function ensureConflictDetail(conflictId: string): Promise<void> {
  if (conflictDetails.value[conflictId] || conflictDetailLoading.value.has(conflictId)) return
  conflictDetailLoading.value = new Set(conflictDetailLoading.value).add(conflictId)
  try {
    const detail = await getConflict(conflictId)
    conflictDetails.value = { ...conflictDetails.value, [conflictId]: detail }
  } catch {
    // 详情失败不阻断：冲突卡仍展示 claim 与证据 id 兜底
  } finally {
    const next = new Set(conflictDetailLoading.value)
    next.delete(conflictId)
    conflictDetailLoading.value = next
  }
}

// 展开中的分歧（ConflictBlock 内部维护展开态，页面同步 id 以触发详情拉取）
const expandedConflictIds = ref<Set<string>>(new Set())

function onConflictToggle(conflictId: string, expanded: boolean): void {
  const next = new Set(expandedConflictIds.value)
  if (expanded) {
    next.add(conflictId)
    void ensureConflictDetail(conflictId)
  } else {
    next.delete(conflictId)
  }
  expandedConflictIds.value = next
}

// 冲突一方证据：证据池完整行优先，缺失时回退详情内嵌摘要
function conflictEvidence(
  conflictId: string,
  evidenceId: string
): EvidenceResponse | ConflictDetailResponse['evidence_a'] | null {
  const pooled = evidenceMap.value.get(evidenceId)
  if (pooled) return pooled
  const detail = conflictDetails.value[conflictId]
  if (!detail) return null
  if (detail.evidence_a.id === evidenceId) return detail.evidence_a
  if (detail.evidence_b.id === evidenceId) return detail.evidence_b
  return null
}

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

// 裁决挂起：paused 且存在待人工裁决的冲突（M2-2；末条裁决后后端自动恢复，无「继续」接口）
const awaitingVerdict = computed(
  () =>
    run.value?.status === 'paused' &&
    state.conflicts.some((item) => item.status === 'awaiting_human')
)
// 软暂停（非澄清、非裁决挂起）才提供继续入口
const canProceed = computed(
  () => run.value?.status === 'paused' && !awaitingClarification.value && !awaitingVerdict.value
)

// 介入可操作窗口：研究进行中或安全点暂停（澄清挂起除外，其时仅可回答澄清）
const interventionActive = computed(
  () => run.value?.status === 'running' || run.value?.status === 'paused'
)
// 追加追问入口仅在证据检索阶段出现（[前端详细设计 §11.3]「阶段3 行内输入」）
const followupAvailable = computed(
  () => interventionActive.value && currentStage.value === 'retrieve'
)
// 剔除入口：软暂停/进行中可剔除；澄清挂起阶段尚无证据不开放
const evidenceExcludable = computed(
  () =>
    run.value?.status === 'running' ||
    (run.value?.status === 'paused' && !awaitingClarification.value)
)

// ─── 介入统一错误文案：错误码映射优先，回退服务端消息与通用标题 ───

function interventionErrorTitle(err: unknown, fallback: string): string {
  const apiError = err as ApiError
  if (apiError?.code && zhCN.interventionErrors[apiError.code]) {
    return zhCN.interventionErrors[apiError.code]
  }
  return apiError?.title || fallback
}

function interventionErrorDescription(err: unknown): string | undefined {
  const apiError = err as ApiError
  return apiError?.detail || apiError?.code || undefined
}

// ─── 统一介入抽屉（澄清 / 追问）───

const drawerOpen = ref(false)
const drawerMode = ref<InterventionMode>('clarify')
const drawerSubmitting = ref(false)

// 澄清帧到达即弹出抽屉：interrupt.requested 先于 run.finished(paused) 约 300ms，
// 不能等待 status 变 paused，否则弹窗条件永不成立；用户可手动关闭，由横幅入口重新打开
watch(
  () => state.interrupt,
  (interrupt) => {
    if (interrupt) {
      drawerMode.value = 'clarify'
      drawerOpen.value = true
    }
  },
  { immediate: true }
)

function openClarifyDrawer(): void {
  drawerMode.value = 'clarify'
  drawerOpen.value = true
}

function openFollowupDrawer(): void {
  drawerMode.value = 'followup'
  drawerOpen.value = true
}

async function submitClarification(answers: Record<string, string>): Promise<void> {
  drawerSubmitting.value = true
  try {
    await actions.submitAnswers(answers)
    drawerOpen.value = false
    toast.success('澄清答案已提交，研究继续推进')
    trackIntervene('clarify', true)
    // resume 后 run 转 running：重取快照并重建实时通道，同时清掉旧 interrupt
    await reload()
  } catch (err) {
    trackIntervene('clarify', false, err)
    toast.danger(interventionErrorTitle(err, '澄清答案提交失败'), {
      description: interventionErrorDescription(err)
    })
  } finally {
    drawerSubmitting.value = false
  }
}

async function submitFollowup(payload: {
  subQuestionId: string | null
  question: string
}): Promise<void> {
  drawerSubmitting.value = true
  try {
    await actions.intervene({
      type: 'ask_followup',
      payload:
        payload.subQuestionId !== null
          ? { sub_question_id: payload.subQuestionId, question: payload.question }
          : { question: payload.question }
    })
    drawerOpen.value = false
    toast.success('追问已提交，将纳入后续检索与分析')
    trackIntervene('followup', true)
  } catch (err) {
    trackIntervene('followup', false, err)
    toast.danger(interventionErrorTitle(err, '追问提交失败'), {
      description: interventionErrorDescription(err)
    })
  } finally {
    drawerSubmitting.value = false
  }
}

// ─── 证据剔除与可恢复列 ───

const pendingExcludeIds = ref<Set<string>>(new Set())

// 实时态中的已剔除证据（含关闭通道后的 REST 全量快照）
const liveExcluded = computed(() => state.evidence.filter((item) => item.excluded_by_user))

// 一旦出现剔除项即拉取含剔除项的全量列表，供刷新/终态后恢复列仍可见
watch(
  () => liveExcluded.value.length,
  (count) => {
    if (count > 0) void excludedList.refresh()
  },
  { immediate: true }
)

// 可恢复列：REST（含剔除项）与实时态并集，仅保留 excluded_by_user 行
const excludedRows = computed<EvidenceResponse[]>(() => {
  const map = new Map<string, EvidenceResponse>()
  for (const item of excludedList.items.value) {
    if (item.excluded_by_user) map.set(item.id, item)
  }
  for (const item of liveExcluded.value) map.set(item.id, item)
  return Array.from(map.values())
})

async function excludeEvidence(evidenceId: string): Promise<void> {
  if (pendingExcludeIds.value.has(evidenceId)) return
  pendingExcludeIds.value = new Set(pendingExcludeIds.value).add(evidenceId)
  try {
    await actions.intervene({
      type: 'exclude_evidence',
      payload: { evidence_id: evidenceId }
    })
    toast.success('证据已从研究中剔除', { description: '可在证据流底部「已剔除证据」中恢复' })
    trackIntervene('exclude', true)
    // 主列表重拉收敛分页 total；可恢复列由 liveExcluded watch 统一驱动，避免重复拉取造成区块抖动
    await evidenceList.refresh()
  } catch (err) {
    trackIntervene('exclude', false, err)
    toast.danger(interventionErrorTitle(err, '剔除证据失败'), {
      description: interventionErrorDescription(err)
    })
  } finally {
    const next = new Set(pendingExcludeIds.value)
    next.delete(evidenceId)
    pendingExcludeIds.value = next
  }
}

async function restoreEvidence(evidence: EvidenceResponse): Promise<void> {
  if (pendingExcludeIds.value.has(evidence.id)) return
  pendingExcludeIds.value = new Set(pendingExcludeIds.value).add(evidence.id)
  try {
    // excluded=false 为恢复语义（mock 先行形态，与后端 M2-5 冻结契约对齐时复核）
    await actions.intervene({
      type: 'exclude_evidence',
      payload: { evidence_id: evidence.id, excluded: false }
    })
    toast.success(`《${evidence.title}》已恢复到证据流`)
    // 实时态 patch 后可恢复列经 computed 立即移除；只需重拉主列表让证据回到分页结果
    await evidenceList.refresh()
  } catch (err) {
    toast.danger(interventionErrorTitle(err, '恢复证据失败'), {
      description: interventionErrorDescription(err)
    })
  } finally {
    const next = new Set(pendingExcludeIds.value)
    next.delete(evidence.id)
    pendingExcludeIds.value = next
  }
}

// ─── 暂停（软暂停，模态确认）与继续 ───

const pauseDialogOpen = ref(false)
const pauseSubmitting = ref(false)

// 软暂停起点（毫秒时间戳）：暂停成功时记录；页面挂载时已处于软暂停则以 updated_at 近似
const pausedSince = ref<number | null>(null)

watch(
  () => run.value?.status,
  (status, oldStatus) => {
    if (status === 'paused' && !awaitingClarification.value) {
      if (pausedSince.value === null) {
        pausedSince.value = oldStatus ? Date.now() : new Date(run.value?.updated_at ?? Date.now()).getTime()
      }
    } else if (status === 'running' || status === 'succeeded' || status === 'cancelled' || status === 'failed') {
      pausedSince.value = null
    }
  },
  { immediate: true }
)

// 「继续」二次确认弹窗（暂停超 15 分钟）
const resumeDialogOpen = ref(false)
const resumeSubmitting = ref(false)

function openPauseDialog(): void {
  pauseDialogOpen.value = true
}

async function confirmPause(): Promise<void> {
  pauseSubmitting.value = true
  try {
    await actions.pause()
    pauseDialogOpen.value = false
    pausedSince.value = Date.now()
    toast.success('研究已暂停，系统将在当前安全点停止推进')
    trackIntervene('pause', true)
    // 立即重取快照收敛 paused 态（暂停后实时通道关闭）
    await reload()
  } catch (err) {
    trackIntervene('pause', false, err)
    toast.danger(interventionErrorTitle(err, '暂停失败'), {
      description: interventionErrorDescription(err)
    })
  } finally {
    pauseSubmitting.value = false
  }
}

function onProceedClick(): void {
  if (resumeSubmitting.value) return
  if (
    pausedSince.value !== null &&
    Date.now() - pausedSince.value > RESUME_CONFIRM_AFTER_MS
  ) {
    resumeDialogOpen.value = true
    return
  }
  void confirmProceed()
}

async function confirmProceed(): Promise<void> {
  resumeSubmitting.value = true
  try {
    await actions.proceed()
    resumeDialogOpen.value = false
    toast.success('研究已继续推进')
    trackIntervene('resume', true)
    await reload()
  } catch (err) {
    trackIntervene('resume', false, err)
    toast.danger(interventionErrorTitle(err, '继续研究失败'), {
      description: interventionErrorDescription(err)
    })
  } finally {
    resumeSubmitting.value = false
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
      auto-retry
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
              v-if="canProceed"
              size="sm"
              :loading="resumeSubmitting"
              @click="onProceedClick"
            >
              继续
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
          <div class="cockpit-paused__body">
            <template v-if="awaitingClarification">
              <p class="cockpit-paused__title">
                研究已暂停：等待澄清确认
              </p>
              <p class="cockpit-paused__message">
                系统在「{{ stageLabel(pausedStageName) }}」阶段需要补充信息后才能继续。
              </p>
            </template>
            <template v-else-if="awaitingVerdict">
              <p class="cockpit-paused__title">
                研究已暂停：等待冲突裁决
              </p>
              <p class="cockpit-paused__message">
                交叉审校发现需要人工裁决的冲突，完成裁决后系统将自动继续，无需手动恢复。
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
          <div class="cockpit-paused__actions">
            <UiButton
              v-if="awaitingClarification && state.interrupt"
              size="sm"
              @click="openClarifyDrawer"
            >
              回答澄清问题
            </UiButton>
            <UiButton
              v-else-if="canProceed"
              size="sm"
              :loading="resumeSubmitting"
              @click="onProceedClick"
            >
              继续研究
            </UiButton>
          </div>
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
                  :evidence-a="conflictEvidence(conflict.id, conflict.evidence_a_id)"
                  :evidence-b="conflictEvidence(conflict.id, conflict.evidence_b_id)"
                  @toggle="onConflictToggle"
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
                <span class="cockpit-evidence__head-actions">
                  <UiButton
                    v-if="followupAvailable"
                    variant="secondary"
                    size="sm"
                    @click="openFollowupDrawer"
                  >
                    追加追问
                  </UiButton>
                  <UiBadge variant="neutral">
                    {{ evidenceList.total.value }}
                  </UiBadge>
                </span>
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
                      :excludable="evidenceExcludable"
                      :excluding="pendingExcludeIds.has(evidence.id)"
                      @toggle="toggleEvidence"
                      @exclude="excludeEvidence"
                    />
                  </li>
                </ul>

                <!-- 已剔除证据可恢复列（[前端详细设计 §11.3]：剔除隐藏、可恢复） -->
                <section
                  v-if="excludedRows.length > 0"
                  class="cockpit-excluded"
                >
                  <h3 class="cockpit-excluded__title">
                    已剔除证据（{{ excludedRows.length }}）
                  </h3>
                  <ul class="cockpit-excluded__list">
                    <li
                      v-for="evidence in excludedRows"
                      :key="evidence.id"
                      class="cockpit-excluded__item"
                    >
                      <span
                        class="cockpit-excluded__name"
                        :title="evidence.title"
                      >{{ evidence.title }}</span>
                      <UiButton
                        variant="secondary"
                        size="sm"
                        :loading="pendingExcludeIds.has(evidence.id)"
                        @click="restoreEvidence(evidence)"
                      >
                        恢复
                      </UiButton>
                    </li>
                  </ul>
                </section>

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

    <!-- 继续确认：暂停超 15 分钟二次确认（[前端详细设计 §11.3]） -->
    <UiDialog
      v-model="resumeDialogOpen"
      size="sm"
      title="继续研究"
      :close-on-mask="!resumeSubmitting"
      :close-on-esc="!resumeSubmitting"
    >
      <p class="cockpit-pause-dialog__text">
        本次研究已暂停超过 15 分钟，确认从当前安全点继续推进吗？
      </p>
      <p class="cockpit-pause-dialog__text">
        继续后系统将沿用已采集的证据与子问题进度，无需重新开始。
      </p>
      <template #footer>
        <UiButton
          variant="secondary"
          size="sm"
          :disabled="resumeSubmitting"
          @click="resumeDialogOpen = false"
        >
          暂不继续
        </UiButton>
        <UiButton
          size="sm"
          :loading="resumeSubmitting"
          @click="confirmProceed"
        >
          确认继续
        </UiButton>
      </template>
    </UiDialog>

    <!-- 统一介入抽屉：阶段1 澄清卡 / 阶段3 追加追问 -->
    <InterventionDrawer
      v-model="drawerOpen"
      :mode="drawerMode"
      :interrupt="state.interrupt"
      :sub-questions="state.subQuestions"
      :submitting="drawerSubmitting"
      @submit-answers="submitClarification"
      @submit-followup="submitFollowup"
    />
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.cockpit-paused__body {
  min-width: 0;
}

.cockpit-paused__actions {
  flex: 0 0 auto;
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

.cockpit-evidence__head-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.cockpit-excluded {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
}

.cockpit-excluded__title {
  margin: 0 0 var(--space-2);
  font-size: var(--font-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}

.cockpit-excluded__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.cockpit-excluded__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--neutral-100);
}

.cockpit-excluded__name {
  min-width: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
