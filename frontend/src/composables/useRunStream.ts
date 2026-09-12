// 单个研究运行的实时状态编排（[前端详细设计 §7.3 M2 全量版]）
// 模块级 Map 缓存 reactive 状态：指挥舱/报告等多页面订阅同一 run 共享状态与同一条 WS
// （RealtimeClient 引用计数，跨页切换不重建连接）；页面卸载只解绑，终态帧保留在缓存。
import { onMounted, onUnmounted, reactive, computed, type ComputedRef } from 'vue'
import { getRun } from '@/services/api/runs'
import {
  getCostSnapshot,
  getRunConflicts,
  getRunStages,
  getRunSubQuestions,
  listRunEvidence
} from '@/services/api/dashboard'
import type {
  ConflictResponse,
  CostSnapshot,
  EvidenceResponse,
  ResearchStageName,
  RunResponse,
  RunStatus,
  StageResponse,
  SubQuestionResponse
} from '@/services/api/types'
import { realtimeClient, type WsChannel } from '@/services/realtime/realtime'
import type { ChannelState } from '@/services/realtime/types'
import { buildRunStreamUrl } from '@/services/api/runs'
import { pauseRun, proceedRun, submitClarificationAnswers } from '@/services/api/interventions'
import {
  REALTIME_EVENT,
  type ClarificationQuestion,
  type ConflictDetectedPayload,
  type CostWarningPayload,
  type EvidenceFetchedPayload,
  type InterruptRequestedPayload,
  type RealtimeEnvelope,
  type RunFinishedPayload,
  type StageFailedPayload,
  type SubQuestionLifecyclePayload,
  type TokenUsagePayload
} from '@/services/realtime/types'
import { useSessionStore } from '@/stores/session'
import { RESEARCH_STAGES } from '@/services/domain/stages'

// run 进入通道关闭后不再产生实时事件：succeeded/failed/cancelled 终态 + paused 澄清挂起
// （paused 的恢复在 M2 HITL，恢复前不建 WS 也不轮询，避免无意义流量）
const STREAM_CLOSED_STATUSES: ReadonlySet<RunStatus> = new Set([
  'succeeded',
  'failed',
  'cancelled',
  'paused'
])

// 非关闭态兜底 REST 轮询间隔（M2 WP-13：仅通道 retrying 时启用，避免 live 期间高频重复拉取）
const REST_POLL_INTERVAL_MS = 4000
// 重连补齐时证据池首帧拉取上限：覆盖最近增量即可，更早分页由 useEvidenceList 按需加载
const EVIDENCE_ALIGN_PAGE_SIZE = 50
// 证据增量 rAF 合批窗口（[§9.4]）
// （同一帧内多条 evidence.fetched 合并一次响应式写入）

export interface StageRuntime {
  name: ResearchStageName
  status: 'pending' | 'running' | 'done' | 'failed'
  attempt: number | null
  startedAt: number | null
  errorCode?: string
  errorMessage?: string
  // 后端标记可重试（stage.failed.retryable），M2-5 前仅记录，不提供按钮
  retryable?: boolean
}

// 子问题进度派生（WP-14 SubQuestionPlan 的 m/n 与异常态口径）
export interface SubQuestionStats {
  total: number
  queued: number
  running: number
  succeeded: number
  failed: number
  evidenceShort: number
  // 已完成（成功）数 m
  completed: number
  // 全部结束（成功/失败/证据不足均算终态）
  allSettled: boolean
}

export interface StageStats {
  total: number
  completed: number
  currentIndex: number
  currentName: ResearchStageName | null
}

export interface ConflictStats {
  total: number
  // 未解决（detected/awaiting_human），看板红点计数
  unresolved: number
}

// 随状态缓存的派生集合（模块级单例，跨页面订阅不重复创建 computed）
export interface RunStreamDerived {
  subQuestions: ComputedRef<SubQuestionStats>
  stages: ComputedRef<StageStats>
  conflicts: ComputedRef<ConflictStats>
}

export interface CostState {
  used: number
  budget: number
  ratio: number
  // 当前预警级别（null=正常；warning=70%；danger=90%）
  warningLevel: 'warning' | 'danger' | null
}

export interface RunStreamState {
  run: RunResponse | null
  stages: StageRuntime[]
  subQuestions: SubQuestionResponse[]
  evidence: EvidenceResponse[]
  conflicts: ConflictResponse[]
  cost: CostState
  // 进行中的澄清/裁决介入请求（interrupt.requested）；M2 WP-15 渲染卡片
  interrupt: (InterruptRequestedPayload & { stage?: string }) | null
  channelState: ChannelState
  loading: boolean
  notFound: boolean
  error: ApiErrorLike | null
}

interface ApiErrorLike {
  status: number
  code: string
  title: string
  detail?: string
}

const TERMINAL_RUN_STATUSES: ReadonlySet<RunStatus> = STREAM_CLOSED_STATUSES

function createInitialCost(run?: RunResponse | null): CostState {
  return {
    used: run?.token_used ?? 0,
    budget: run?.token_budget ?? 0,
    ratio: run?.token_budget ? Math.min(1, (run.token_used ?? 0) / run.token_budget) : 0,
    warningLevel: null
  }
}

function createInitialState(): RunStreamState {
  return {
    run: null,
    stages: RESEARCH_STAGES.map((name) => ({
      name,
      status: 'pending',
      attempt: null,
      startedAt: null
    })),
    subQuestions: [],
    evidence: [],
    conflicts: [],
    cost: createInitialCost(),
    interrupt: null,
    channelState: 'idle',
    loading: false,
    notFound: false,
    error: null
  }
}

// 模块级缓存：runId -> 共享响应式状态
const stateCache = new Map<string, RunStreamState>()
// runId -> 派生统计（随状态缓存同生命周期）
const derivedCache = new Map<string, RunStreamDerived>()

function ensureState(runId: string): RunStreamState {
  const cached = stateCache.get(runId)
  if (cached) return cached
  const state = reactive(createInitialState())
  stateCache.set(runId, state)
  derivedCache.set(runId, createDerived(state))
  return state
}

// 只读窥视口：供不同时挂载 useRunStream 的消费方（useEvidenceList）读取共享实时态，
// 不触发任何生命周期或连接；无缓存时返回 null
export function peekRunStreamState(runId: string): RunStreamState | null {
  return stateCache.get(runId) ?? null
}

// 派生统计：m/n 进度、异常态计数均在响应式层计算，WP-14 直接渲染不重复派生
function createDerived(state: RunStreamState): RunStreamDerived {
  return {
    subQuestions: computed<SubQuestionStats>(() => {
      const list = state.subQuestions
      const stats: SubQuestionStats = {
        total: list.length,
        queued: 0,
        running: 0,
        succeeded: 0,
        failed: 0,
        evidenceShort: 0,
        completed: 0,
        allSettled: list.length > 0
      }
      for (const item of list) {
        if (item.status === 'queued' || item.status === 'pending') stats.queued += 1
        else if (item.status === 'running') stats.running += 1
        else if (item.status === 'succeeded') stats.succeeded += 1
        else if (item.status === 'failed') stats.failed += 1
        else if (item.status === 'evidence_short') stats.evidenceShort += 1
        if (item.status === 'pending' || item.status === 'queued' || item.status === 'running') {
          stats.allSettled = false
        }
      }
      stats.completed = stats.succeeded
      return stats
    }),
    stages: computed<StageStats>(() => {
      const completed = state.stages.filter((s) => s.status === 'done').length
      const running = state.stages.find((s) => s.status === 'running')
      const currentIndex = running ? state.stages.indexOf(running) : -1
      return {
        total: RESEARCH_STAGES.length,
        completed,
        currentIndex,
        currentName: running?.name ?? null
      }
    }),
    conflicts: computed<ConflictStats>(() => {
      const list = state.conflicts
      return {
        total: list.length,
        unresolved: list.filter((c) => c.status === 'detected' || c.status === 'awaiting_human').length
      }
    })
  }
}

// run 快照映射六阶段状态（REST 初帧/补帧/兜底轮询共用）
// 前置截断 current_stage：paused 归入通道关闭态，其所在阶段按"进行中"语义保留
function mapStages(run: RunResponse, prev: StageRuntime[]): StageRuntime[] {
  const currentIndex = run.current_stage ? RESEARCH_STAGES.indexOf(run.current_stage) : -1
  return RESEARCH_STAGES.map((name, i) => {
    const previous = prev.find((item) => item.name === name) ?? {
      name,
      status: 'pending' as const,
      attempt: null,
      startedAt: null
    }
    if (run.status === 'succeeded') {
      return { ...previous, name, status: 'done' }
    }
    if (run.status === 'failed') {
      if (i === currentIndex) {
        return {
          ...previous,
          name,
          status: 'failed',
          errorCode: run.error_code ?? undefined,
          errorMessage: run.error_message ?? undefined
        }
      }
      if (i < currentIndex) {
        return { ...previous, name, status: 'done' }
      }
      return { ...previous, name, status: 'pending' }
    }
    if (run.status === 'cancelled') {
      if (i < currentIndex) return { ...previous, name, status: 'done' }
      return { ...previous, name, status: 'pending' }
    }
    // running / pending / paused：按当前阶段截断渲染
    if (currentIndex === -1) return { ...previous, name, status: 'pending' }
    if (i < currentIndex) return { ...previous, name, status: 'done' }
    if (i === currentIndex) return { ...previous, name, status: 'running' }
    return { ...previous, name, status: 'pending' }
  })
}

export function useRunStream(runId: string) {
  const session = useSessionStore()
  const state = ensureState(runId)

  let channel: WsChannel | null = null
  const unsubscribe: Array<() => void> = []
  let disposed = false
  let pollTimer: ReturnType<typeof setInterval> | null = null

  // 证据增量 rAF 合批队列
  let evidenceFrame: EvidenceFetchedPayload[] = []
  let evidenceRafId: number | null = null

  // ─── 快照应用 ───

  function applyCost(cost: Partial<CostState> & { used?: number; budget?: number; ratio?: number }): void {
    if (typeof cost.used === 'number') state.cost.used = cost.used
    if (typeof cost.budget === 'number') state.cost.budget = cost.budget
    if (typeof cost.ratio === 'number') state.cost.ratio = cost.ratio
    if (cost.warningLevel !== undefined) state.cost.warningLevel = cost.warningLevel
  }

  function applyRun(run: RunResponse): void {
    state.run = run
    state.stages = mapStages(run, state.stages)
    applyCost({
      used: run.token_used,
      budget: run.token_budget,
      ratio: run.token_budget ? Math.min(1, run.token_used / run.token_budget) : state.cost.ratio
    })
    if (STREAM_CLOSED_STATUSES.has(run.status)) {
      detach('destroy')
    }
  }

  // 通道 live 后补 GET：对齐订阅建立前漏帧（[§9.3] 补齐流程）
  async function alignWithRest(): Promise<void> {
    try {
      applyRun(await getRun(runId))
      if (STREAM_CLOSED_STATUSES.has(state.run!.status)) return
      // M2-4 看板端点未上线前这些请求可能 404：各自独立静默，端点就绪后自动生效
      await Promise.allSettled([
        syncStages(),
        syncSubQuestions(),
        syncConflicts(),
        syncCost(),
        syncEvidence()
      ])
    } catch {
      // 等待 live 重连或 retrying 轮询对齐，补帧失败不打断实时视图
    }
  }

  // 证据重连补齐：拉最近一页与实时增量做稳定 key 合并（更早分页由 useEvidenceList 按需加载）
  async function syncEvidence(): Promise<void> {
    const page = await listRunEvidence(runId, {
      page: 1,
      page_size: EVIDENCE_ALIGN_PAGE_SIZE,
      include_excluded: true
    })
    state.evidence = mergeEvidence(state.evidence, page.items)
  }

  async function syncStages(): Promise<void> {
    const stages: StageResponse[] = await getRunStages(runId)
    for (const s of stages) {
      const idx = RESEARCH_STAGES.indexOf(s.name)
      if (idx === -1) continue
      const prev = state.stages[idx]
      state.stages[idx] = {
        name: s.name,
        status:
          s.status === 'succeeded'
            ? 'done'
            : s.status === 'skipped'
              ? 'done'
              : s.status === 'failed'
                ? 'failed'
                : s.status === 'running'
                  ? 'running'
                  : 'pending',
        attempt: s.attempt ?? prev.attempt,
        startedAt: prev.startedAt,
        errorCode: prev.errorCode,
        errorMessage: prev.errorMessage
      }
    }
  }

  async function syncSubQuestions(): Promise<void> {
    const items = await getRunSubQuestions(runId)
    state.subQuestions = mergeSubQuestions(state.subQuestions, items)
  }

  async function syncConflicts(): Promise<void> {
    const items = await getRunConflicts(runId)
    state.conflicts = mergeConflicts(state.conflicts, items)
  }

  async function syncCost(): Promise<void> {
    const snapshot: CostSnapshot = await getCostSnapshot(runId)
    applyCost({ used: snapshot.used, budget: snapshot.budget, ratio: snapshot.ratio })
  }

  // ─── 事件处理（§9.2 路由表）───

  function handleStageStarted(env: RealtimeEnvelope): void {
    const payload = env.payload as { stage?: ResearchStageName; attempt?: number }
    const stage = env.stage ?? payload.stage
    if (!stage || !RESEARCH_STAGES.includes(stage)) return
    const index = RESEARCH_STAGES.indexOf(stage)
    state.stages = state.stages.map((item, i) => {
      if (i < index && item.status !== 'failed') return { ...item, status: 'done' }
      if (i === index) {
        return { ...item, status: 'running', attempt: payload.attempt ?? item.attempt ?? 1, startedAt: env.ts ?? item.startedAt }
      }
      return item
    })
    if (state.run) {
      state.run.current_stage = stage as ResearchStageName
      if (state.run.status === 'pending') state.run.status = 'running'
    }
  }

  function handleStageFinished(env: RealtimeEnvelope): void {
    const payload = env.payload as { stage?: ResearchStageName }
    const stage = env.stage ?? payload.stage
    if (!stage) return
    const index = RESEARCH_STAGES.indexOf(stage)
    if (index === -1) return
    state.stages = state.stages.map((item, i) => (i === index ? { ...item, status: 'done' } : item))
  }

  function handleStageFailed(env: RealtimeEnvelope): void {
    const payload = env.payload as StageFailedPayload
    const stage = env.stage ?? payload.stage
    if (!stage) return
    const index = RESEARCH_STAGES.indexOf(stage)
    if (index === -1) return
    state.stages = state.stages.map((item, i) =>
      i === index
        ? {
            ...item,
            status: 'failed',
            errorCode: payload.error_code,
            errorMessage: payload.error_message,
            retryable: payload.retryable
          }
        : item
    )
  }

  function handleSubQuestion(env: RealtimeEnvelope): void {
    const payload = env.payload as SubQuestionLifecyclePayload
    if (!payload.sub_question_id) return
    const next: SubQuestionResponse = {
      id: payload.sub_question_id,
      run_id: runId,
      question: payload.question ?? '',
      depends_on: payload.depends_on ?? [],
      status: payload.status ?? 'queued',
      evidence_count: payload.evidence_count ?? 0
    }
    state.subQuestions = mergeSubQuestions(state.subQuestions, [next])
  }

  function queueEvidence(env: RealtimeEnvelope): void {
    const payload = env.payload as EvidenceFetchedPayload
    if (!payload?.id) return
    evidenceFrame.push(payload)
    if (evidenceRafId === null && typeof requestAnimationFrame === 'function') {
      evidenceRafId = requestAnimationFrame(flushEvidenceFrame)
    } else if (evidenceRafId === null) {
      // 非浏览器帧环境兜底：微任务后立即 flush
      evidenceRafId = 0
      void Promise.resolve().then(flushEvidenceFrame)
    }
  }

  function flushEvidenceFrame(): void {
    evidenceRafId = null
    const batch = evidenceFrame
    evidenceFrame = []
    const incoming: EvidenceResponse[] = batch.map((p) => ({
      id: p.id,
      run_id: runId,
      sub_question_id: p.sub_question_id,
      url: p.url,
      domain: p.domain,
      title: p.title,
      snippet: p.snippet,
      source_type: p.source_type,
      source_level: p.source_level,
      credibility: p.credibility,
      relevance_score: p.relevance_score,
      published_at: p.published_at ?? null,
      excluded_by_user: p.excluded_by_user ?? false
    }))
    state.evidence = mergeEvidence(state.evidence, incoming)
  }

  function handleInterrupt(env: RealtimeEnvelope): void {
    const payload = env.payload as InterruptRequestedPayload
    if (!payload || !Array.isArray(payload.questions)) return
    state.interrupt = {
      reason: payload.reason,
      questions: payload.questions as ClarificationQuestion[],
      defaults: payload.defaults ?? {},
      expires_in_seconds: payload.expires_in_seconds,
      stage: env.stage
    }
  }

  function handleConflict(env: RealtimeEnvelope): void {
    const payload = env.payload as ConflictDetectedPayload
    if (!payload?.id) return
    const next: ConflictResponse = {
      id: payload.id,
      run_id: runId,
      claim: payload.claim,
      evidence_a_id: payload.evidence_a_id,
      evidence_b_id: payload.evidence_b_id,
      type: payload.type,
      severity: payload.severity,
      status: payload.status
    }
    state.conflicts = mergeConflicts(state.conflicts, [next])
  }

  function handleTokenUsage(env: RealtimeEnvelope): void {
    const payload = env.payload as TokenUsagePayload
    applyCost({
      used: payload.used,
      budget: payload.budget ?? state.cost.budget,
      ratio: payload.budget ? payload.used / payload.budget : payload.used / (state.cost.budget || 1)
    })
    if (state.run) state.run.token_used = payload.used
  }

  function handleCostWarning(env: RealtimeEnvelope): void {
    const payload = env.payload as CostWarningPayload
    applyCost({
      used: payload.used,
      budget: payload.budget,
      ratio: payload.ratio,
      warningLevel: payload.level
    })
  }

  // 应用 run 结束事件（run.finished/run.failed）；状态以事件负载 status 为准
  function applyTerminalPayload(
    payload: RunFinishedPayload,
    status: Extract<RunStatus, 'succeeded' | 'failed' | 'cancelled' | 'paused'>
  ): void {
    const run = state.run
    if (!run) return
    run.status = status
    if (payload.current_stage) {
      run.current_stage = payload.current_stage as ResearchStageName
    }
    if (typeof payload.token_used === 'number') {
      run.token_used = payload.token_used
      // 终态 token 是最终权威值，同步成本卡状态，避免停在最后一条 token.usage 增量帧
      applyCost({
        used: payload.token_used,
        budget: run.token_budget,
        ratio: run.token_budget ? payload.token_used / run.token_budget : 0
      })
    }
    if (payload.error_code !== undefined) run.error_code = payload.error_code
    if (payload.error_message !== undefined) run.error_message = payload.error_message
    state.stages = mapStages(run, state.stages)
    detach('destroy')
  }

  function handleRunFinished(env: RealtimeEnvelope): void {
    const payload = env.payload as RunFinishedPayload
    applyTerminalPayload(payload, payload.status)
  }

  function handleRunFailed(env: RealtimeEnvelope): void {
    const payload = env.payload as RunFinishedPayload
    applyTerminalPayload(payload, 'failed')
  }

  // ─── 实时通道与轮询 ───

  // 本实例解绑：release=仅释放引用（跨页共享，RealtimeClient 延迟 5s 断开）；
  // destroy=终态/异常立即关闭（不等待引用归零）
  function detach(mode: 'release' | 'destroy'): void {
    for (const off of unsubscribe) off()
    unsubscribe.length = 0
    stopPolling()
    if (evidenceRafId !== null && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(evidenceRafId)
    }
    evidenceRafId = null
    evidenceFrame = []
    if (channel) {
      const channelId = `runs:${runId}`
      if (mode === 'destroy') {
        realtimeClient.destroyChannel(channelId)
        state.channelState = 'idle'
      } else {
        realtimeClient.releaseChannel(channelId)
      }
      channel = null
    }
  }

  function connectStream(): void {
    // 重连/重试前先释放本实例旧引用（共享连接若仍被其他页面持有则不断开）
    detach('release')

    const token = session.accessToken
    if (!token) {
      throw new Error('建立实时连接前访问令牌缺失')
    }

    const channelId = `runs:${runId}`
    channel = realtimeClient.ensureChannel(channelId, { url: buildRunStreamUrl(runId, token) })
    channel.setOnStateChange(handleChannelState)
    unsubscribe.push(channel.on(REALTIME_EVENT.STAGE_STARTED, handleStageStarted))
    unsubscribe.push(channel.on(REALTIME_EVENT.STAGE_FINISHED, handleStageFinished))
    unsubscribe.push(channel.on(REALTIME_EVENT.STAGE_FAILED, handleStageFailed))
    unsubscribe.push(channel.on(REALTIME_EVENT.SUB_QUESTION_CREATED, handleSubQuestion))
    unsubscribe.push(channel.on(REALTIME_EVENT.SUB_QUESTION_STARTED, handleSubQuestion))
    unsubscribe.push(channel.on(REALTIME_EVENT.SUB_QUESTION_FINISHED, handleSubQuestion))
    unsubscribe.push(channel.on(REALTIME_EVENT.EVIDENCE_FETCHED, queueEvidence))
    unsubscribe.push(channel.on(REALTIME_EVENT.INTERRUPT_REQUESTED, handleInterrupt))
    unsubscribe.push(channel.on(REALTIME_EVENT.CONFLICT_DETECTED, handleConflict))
    unsubscribe.push(channel.on(REALTIME_EVENT.TOKEN_USAGE_UPDATE, handleTokenUsage))
    unsubscribe.push(channel.on(REALTIME_EVENT.COST_WARNING, handleCostWarning))
    unsubscribe.push(channel.on(REALTIME_EVENT.RUN_FINISHED, handleRunFinished))
    unsubscribe.push(channel.on(REALTIME_EVENT.RUN_FAILED, handleRunFailed))
    channel.connect()
    state.channelState = channel.getState()
    // 初始连接前 init() 已取过快照；按当前通道状态决定是否需要兜底轮询
    syncPollingWithChannel(state.channelState)
  }

  // WP-13 轮询策略：仅 retrying（WS 退避重连中）才定时轮询快照，
  // live 后一次性补帧即停轮询，避免高频重复拉取（M1 为无条件 4s 轮询）
  function handleChannelState(next: ChannelState): void {
    state.channelState = next
    syncPollingWithChannel(next)
    if (next === 'live') void alignWithRest()
  }

  function syncPollingWithChannel(channelState: ChannelState): void {
    if (channelState === 'retrying') {
      if (pollTimer === null) {
        // 立即补一次，再按 4s 间隔兜底，使断线期间看板快速收敛
        void alignWithRest()
        pollTimer = setInterval(() => {
          void alignWithRest()
        }, REST_POLL_INTERVAL_MS)
      }
    } else {
      stopPolling()
    }
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // ─── 初帧加载 ───

  async function init(): Promise<void> {
    const disposedAfter = () => disposed
    state.loading = true
    try {
      const run = await getRun(runId)
      if (disposedAfter()) return
      applyRun(run)
      if (!TERMINAL_RUN_STATUSES.has(run.status)) connectStream()
    } catch (err) {
      const apiError = err as ApiErrorLike
      if (apiError.status === 404) {
        state.notFound = true
      } else {
        state.error = apiError
      }
      detach('destroy')
    } finally {
      state.loading = false
    }
  }

  // HITL 受控动作（M2 WP-15 接 UI；REST 通道，与 WS sendCommand 等价）
  const actions = {
    pause: () => pauseRun(runId),
    proceed: () => proceedRun(runId),
    submitAnswers: (answers: Record<string, string>) => submitClarificationAnswers(runId, answers)
  }

  onMounted(init)
  onUnmounted(() => {
    disposed = true
    // 离开页面只释放引用：其他页面（报告/看板）仍可共享同一条连接
    detach('release')
  })

  return {
    state,
    derived: derivedCache.get(runId)!,
    connect: connectStream,
    disconnect: () => detach('destroy'),
    reload: init,
    actions
  }
}

// ─── 合并工具：稳定 key 增量更新，避免整表重渲染 ───

function mergeSubQuestions(
  prev: SubQuestionResponse[],
  incoming: SubQuestionResponse[]
): SubQuestionResponse[] {
  const map = new Map(prev.map((item) => [item.id, item]))
  for (const item of incoming) {
    const old = map.get(item.id)
    map.set(item.id, old ? { ...old, ...compact(item) } : item)
  }
  return Array.from(map.values())
}

function mergeEvidence(prev: EvidenceResponse[], incoming: EvidenceResponse[]): EvidenceResponse[] {
  const map = new Map(prev.map((item) => [item.id, item]))
  for (const item of incoming) {
    const old = map.get(item.id)
    map.set(item.id, old ? { ...old, ...compact(item) } : item)
  }
  return Array.from(map.values())
}

function mergeConflicts(prev: ConflictResponse[], incoming: ConflictResponse[]): ConflictResponse[] {
  const map = new Map(prev.map((item) => [item.id, item]))
  for (const item of incoming) {
    const old = map.get(item.id)
    map.set(item.id, old ? { ...old, ...compact(item) } : item)
  }
  return Array.from(map.values())
}

// 去掉 undefined 字段，避免增量合入时把已有关键字段覆盖为 undefined
function compact<T extends object>(obj: T): Partial<T> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as Partial<T>
}
