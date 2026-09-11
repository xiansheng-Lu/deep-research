// 单个研究运行的实时状态编排（[前端详细设计 §7.3] 的 M1 裁剪版）
// 进入先 GET /runs/{id} 取初帧并映射六阶段；非终态再建立 WS：
//   - stage.started 推进时间线当前阶段；
//   - run.finished / run.failed 写入终态；
//   - 通道 live 后补一次 GET 对齐订阅前漏帧（重连 live 同样补帧）；
//   - 非终态期间每 4s 兜底轮询 GET：WS 不可用（代理拦截/握手超时）时仍能收敛到终态。
// 模块级 Map 缓存 reactive 状态：指挥舱重进不闪骨架；M1 不做跨页引用计数，
// 页面卸载即解绑订阅并销毁通道，终态帧保留在缓存中（报告页自行走 REST）。
import { onMounted, onUnmounted, reactive } from 'vue'
import { getRun, buildRunStreamUrl } from '@/services/api/runs'
import type { ResearchStageName, RunResponse, RunStatus } from '@/services/api/types'
import type { ApiError } from '@/services/http/error'
import { realtimeClient, type WsChannel } from '@/services/realtime/realtime'
import {
  REALTIME_EVENT,
  type ChannelState,
  type RealtimeEnvelope,
  type RunFinishedPayload,
  type StageStartedPayload
} from '@/services/realtime/types'
import { RESEARCH_STAGES } from '@/services/domain/stages'
import { useSessionStore } from '@/stores/session'

// 兜底轮询间隔：WS 中断或建连超时时，靠 REST 快照持续推进页面并收敛到终态
const REST_POLL_INTERVAL_MS = 4000

// 单个阶段在时间线上的运行态
export interface StageRuntime {
  name: ResearchStageName
  // 待执行 / 进行中 / 完成 / 失败
  status: 'pending' | 'running' | 'done' | 'failed'
  // 尝试次数（M1 事件恒为 1；初帧 REST 不含该信息时为 null）
  attempt: number | null
  // 阶段开始的毫秒时间戳（来自 stage.started 信封 ts）
  startedAt: number | null
}

// 一个 run 的可观察状态（模块缓存中的值即此结构的 reactive 代理）
export interface RunStreamState {
  run: RunResponse | null
  stages: StageRuntime[]
  // 实时通道状态；终态 run 不建通道，恒为 idle
  channelState: ChannelState
  loading: boolean
  notFound: boolean
  error: ApiError | null
}

// 通道关闭态：run 不再产生实时事件，无需建立/保留 WS 与兜底轮询
// succeeded/failed/cancelled 为终态；paused 为澄清挂起，M1 冻结契约无恢复端点（M2 再开放）
const STREAM_CLOSED_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  'succeeded',
  'failed',
  'cancelled',
  'paused'
])

// 模块级缓存：runId -> 共享响应式状态
const stateCache = new Map<string, RunStreamState>()

function createInitialState(): RunStreamState {
  return {
    run: null,
    stages: RESEARCH_STAGES.map((name) => ({
      name,
      status: 'pending',
      attempt: null,
      startedAt: null
    })),
    channelState: 'idle',
    loading: false,
    notFound: false,
    error: null
  }
}

function ensureState(runId: string): RunStreamState {
  const cached = stateCache.get(runId)
  if (cached) return cached
  const state = reactive(createInitialState())
  stateCache.set(runId, state)
  return state
}

// 依据 run 实体快照映射六阶段状态；prev 用于保留 WS 已写入的 attempt/startedAt
function mapStages(run: RunResponse, prev: StageRuntime[]): StageRuntime[] {
  const currentIndex = run.current_stage ? RESEARCH_STAGES.indexOf(run.current_stage) : -1

  return RESEARCH_STAGES.map((name, i): StageRuntime => {
    const previous = prev.find((item) => item.name === name) ?? null
    const base: StageRuntime = {
      name,
      status: 'pending',
      attempt: previous?.attempt ?? null,
      startedAt: previous?.startedAt ?? null
    }

    if (run.status === 'succeeded') {
      base.status = 'done'
      return base
    }

    if (run.status === 'failed') {
      if (currentIndex === -1) return base
      if (i < currentIndex) base.status = 'done'
      else if (i === currentIndex) base.status = 'failed'
      return base
    }

    // running / pending / paused / cancelled：按当前阶段截断渲染
    if (currentIndex === -1) return base
    if (i < currentIndex) base.status = 'done'
    else if (i === currentIndex) base.status = 'running'
    return base
  })
}

export function useRunStream(runId: string) {
  const session = useSessionStore()
  const state = ensureState(runId)

  // 以下为本次页面挂载的通道资源（缓存共享状态，通道随页面生命周期销毁）
  let channel: WsChannel | null = null
  let pollTimer: number | null = null
  const unsubscribe: Array<() => void> = []
  let disposed = false

  // 应用 run 快照到共享状态并切换通道存在性
  function applyRun(run: RunResponse): void {
    state.run = run
    state.stages = mapStages(run, state.stages)
    if (STREAM_CLOSED_STATUSES.has(run.status)) {
      destroyStream()
    }
  }

  // 通道 live 后补 GET：对齐订阅建立前的漏帧；失败则保持事件推进的现有状态
  async function alignWithRest(): Promise<void> {
    try {
      applyRun(await getRun(runId))
    } catch {
      // 等待下一次 live 或后续阶段事件对齐，不用补帧失败打断实时视图
    }
  }

  // 应用 run 结束事件负载（run.finished / run.failed）；status 可能为 paused（澄清挂起）
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
    }
    if (payload.error_code !== undefined) run.error_code = payload.error_code
    if (payload.error_message !== undefined) run.error_message = payload.error_message
    state.stages = mapStages(run, state.stages)
    // run 已终态：销毁通道并停止兜底轮询（服务端随后也会关闭连接）
    destroyStream()
  }

  function handleStageStarted(env: RealtimeEnvelope): void {
    const payload = env.payload as StageStartedPayload
    const stage = env.stage ?? payload.stage
    if (!stage || !RESEARCH_STAGES.includes(stage as ResearchStageName)) return
    const index = RESEARCH_STAGES.indexOf(stage as ResearchStageName)

    state.stages = state.stages.map((item, i) => {
      if (i < index && item.status !== 'failed') {
        return { ...item, status: 'done' }
      }
      if (i === index) {
        return {
          ...item,
          status: 'running',
          attempt: payload.attempt ?? item.attempt ?? 1,
          startedAt: env.ts ?? item.startedAt
        }
      }
      return item
    })

    if (state.run) {
      state.run.current_stage = stage as ResearchStageName
      if (state.run.status === 'pending') state.run.status = 'running'
    }
  }

  function handleRunFinished(env: RealtimeEnvelope): void {
    // 状态以事件负载为准：后端澄清挂起时同样发 run.finished，但 status=paused
    const payload = env.payload as RunFinishedPayload
    applyTerminalPayload(payload, payload.status)
  }

  function handleRunFailed(env: RealtimeEnvelope): void {
    const payload = env.payload as RunFinishedPayload
    applyTerminalPayload(payload, 'failed')
  }

  // 建立（或重建）实时通道与订阅
  function connectStream(): void {
    destroyStream()

    // 路由守卫已保证登录并恢复令牌；此处为空属程序装配错误，直接抛出
    const token = session.accessToken
    if (!token) {
      throw new Error('建立实时连接前访问令牌缺失')
    }

    const channelId = `runs:${runId}`
    channel = realtimeClient.ensureChannel(channelId, {
      url: buildRunStreamUrl(runId, token)
    })
    channel.setOnStateChange((next) => {
      state.channelState = next
      // 首次连接与每次重连 live 后都补一次 REST，消除订阅空窗漏帧
      if (next === 'live') void alignWithRest()
    })
    unsubscribe.push(channel.on(REALTIME_EVENT.STAGE_STARTED, handleStageStarted))
    unsubscribe.push(channel.on(REALTIME_EVENT.RUN_FINISHED, handleRunFinished))
    unsubscribe.push(channel.on(REALTIME_EVENT.RUN_FAILED, handleRunFailed))
    channel.connect()
    state.channelState = channel.getState()
    startPolling()
  }

  // 兜底轮询：WS 建连失败或事件丢帧时，REST 快照仍能推进阶段并收敛到终态
  function startPolling(): void {
    stopPolling()
    pollTimer = window.setInterval(() => {
      void alignWithRest()
    }, REST_POLL_INTERVAL_MS)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // 解绑订阅并销毁通道（幂等）
  function destroyStream(): void {
    for (const off of unsubscribe) off()
    unsubscribe.length = 0
    stopPolling()
    if (channel) {
      realtimeClient.destroyChannel(channel.id)
      channel = null
    }
    state.channelState = 'idle'
  }

  // 初帧加载：GET 取初帧映射阶段，非终态建 WS；404 单独走不存在态
  async function init(): Promise<void> {
    const firstLoad = state.run === null
    state.loading = firstLoad
    state.notFound = false
    state.error = null
    try {
      const run = await getRun(runId)
      if (disposed) return
      applyRun(run)
      if (!STREAM_CLOSED_STATUSES.has(run.status)) connectStream()
    } catch (err) {
      const apiError = err as ApiError
      if (apiError.status === 404) {
        state.notFound = true
      } else {
        state.error = apiError
      }
      destroyStream()
    } finally {
      state.loading = false
    }
  }

  onMounted(init)
  onUnmounted(() => {
    disposed = true
    destroyStream()
  })

  return {
    state,
    reload: init
  }
}
