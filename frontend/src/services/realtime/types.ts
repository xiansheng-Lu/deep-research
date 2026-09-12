// 实时事件类型定义（[前端详细设计 §9.2 事件路由表]，对齐《后端契约草案》v0.1 §4）
// M2 扩为事件全集；信封形态与 M1 保持一致：{v, event_id, ts, run_id?, stage?, type, payload}
// 注意：本文件被 mock 网关链路（vite.config 的 Node 上下文）引用，必须使用相对路径，禁止 @/ 别名
import type {
  ConflictResponse,
  CostWarningLevel,
  Credibility,
  EvidenceResponse,
  ResearchStageName,
  RunStatus,
  RunTier,
  SourceLevel,
  SourceType,
  SubQuestionStatus
} from '../api/types'

// 心跳：服务端每 30s 发送 ping，客户端须 60s 内回 pong（[§8.3 §9.3]）
export const REALTIME_PROTOCOL_VERSION = '1.0' as const
export type RealtimeProtocolVersion = typeof REALTIME_PROTOCOL_VERSION

// 通道连接状态机（[§9.3]；paused 为页面可见性挂起预留，M2 未启用）
export type ChannelState = 'idle' | 'connecting' | 'live' | 'retrying' | 'paused'

export interface RealtimePing {
  v: string
  type: 'ping'
  ts: number
}

export interface RealtimePong {
  v: string
  type: 'pong'
  ts: number
}

// M2 事件类型常量（服务端 → 前端，§9.2 路由表全集）
export const REALTIME_EVENT = {
  STAGE_STARTED: 'stage.started',
  STAGE_FINISHED: 'stage.finished',
  STAGE_FAILED: 'stage.failed',
  SUB_QUESTION_CREATED: 'sub_question.created',
  SUB_QUESTION_STARTED: 'sub_question.started',
  SUB_QUESTION_FINISHED: 'sub_question.finished',
  EVIDENCE_FETCHED: 'evidence.fetched',
  INTERRUPT_REQUESTED: 'interrupt.requested',
  CONFLICT_DETECTED: 'conflict.detected',
  TOKEN_USAGE_UPDATE: 'token.usage.update',
  COST_WARNING: 'cost.warning',
  REPORT_CHUNK: 'report.chunk',
  REPORT_FINISHED: 'report.finished',
  RUN_FINISHED: 'run.finished',
  RUN_FAILED: 'run.failed',
  // 客户端指令应答
  INTERVENE_ACK: 'intervene.ack',
  INTERVENE_ERROR: 'intervene.error'
} as const

// ─── 事件 payload ───

export interface StageStartedPayload {
  stage?: ResearchStageName
  attempt?: number
}

export interface StageFinishedPayload {
  stage?: ResearchStageName
  attempt?: number
}

export interface StageFailedPayload {
  stage?: ResearchStageName
  attempt?: number
  error_code?: string
  error_message?: string
  // 后端判定可恢复（如 PROVIDER_UNAVAILABLE）时为 true
  retryable?: boolean
}

// 子问题生命周期事件（created 带 question；started/finished 以状态推进为主）
export interface SubQuestionLifecyclePayload {
  sub_question_id: string
  question?: string
  status?: SubQuestionStatus
  evidence_count?: number
  depends_on?: string[]
}

// evidence.fetched 增量事件（§9.4：只带列表展示字段，全文懒加载）
export type EvidenceFetchedPayload = Pick<
  EvidenceResponse,
  | 'id'
  | 'sub_question_id'
  | 'url'
  | 'domain'
  | 'title'
  | 'snippet'
  | 'source_type'
  | 'source_level'
  | 'credibility'
  | 'relevance_score'
> & {
  published_at?: string | null
  excluded_by_user?: boolean
}

// 澄清问题项（契约草案 §4.1）
export interface ClarificationQuestion {
  key: string
  text: string
  options: string[]
  // options 下标，无推荐时为 null
  recommended: number | null
}

// interrupt.requested 定版 payload（契约草案 §4.1，前端只依赖 questions[]/recommended）
export interface InterruptRequestedPayload {
  reason: string
  questions: ClarificationQuestion[]
  defaults: Record<string, string>
  expires_in_seconds: number
}

// conflict.detected：以冲突实体子集承载（详情走 GET /conflicts/{id}）
export type ConflictDetectedPayload = Pick<
  ConflictResponse,
  'id' | 'claim' | 'evidence_a_id' | 'evidence_b_id' | 'type' | 'severity' | 'status'
>

// token.usage.update（服务端 1s 节流，§9.2/§9.4）
export interface TokenUsagePayload {
  used: number
  budget: number
  model_breakdown?: Record<string, number>
}

// cost.warning（契约草案 §4.2）
export interface CostWarningPayload {
  level: CostWarningLevel
  used: number
  budget: number
  ratio: number
}

// report.finished（M2 不恢复 SSE；该事件经 WS 通知终稿可读）
export interface ReportFinishedPayload {
  report_id?: string
  status?: 'draft' | 'final'
}

// run.finished / run.failed（M1 已定版：澄清挂起时 status=paused）
export interface RunFinishedPayload {
  status: 'succeeded' | 'failed' | 'cancelled' | 'paused'
  current_stage?: string | null
  token_used?: number
  error_code?: string | undefined
  error_message?: string | undefined
}

// envelope 各 type 对应的 payload 映射
export interface RealtimePayloadMap {
  [REALTIME_EVENT.STAGE_STARTED]: StageStartedPayload
  [REALTIME_EVENT.STAGE_FINISHED]: StageFinishedPayload
  [REALTIME_EVENT.STAGE_FAILED]: StageFailedPayload
  [REALTIME_EVENT.SUB_QUESTION_CREATED]: SubQuestionLifecyclePayload
  [REALTIME_EVENT.SUB_QUESTION_STARTED]: SubQuestionLifecyclePayload
  [REALTIME_EVENT.SUB_QUESTION_FINISHED]: SubQuestionLifecyclePayload
  [REALTIME_EVENT.EVIDENCE_FETCHED]: EvidenceFetchedPayload
  [REALTIME_EVENT.INTERRUPT_REQUESTED]: InterruptRequestedPayload
  [REALTIME_EVENT.CONFLICT_DETECTED]: ConflictDetectedPayload
  [REALTIME_EVENT.TOKEN_USAGE_UPDATE]: TokenUsagePayload
  [REALTIME_EVENT.COST_WARNING]: CostWarningPayload
  [REALTIME_EVENT.REPORT_CHUNK]: { chunk_id?: string; delta?: string; position?: number }
  [REALTIME_EVENT.REPORT_FINISHED]: ReportFinishedPayload
  [REALTIME_EVENT.RUN_FINISHED]: RunFinishedPayload
  [REALTIME_EVENT.RUN_FAILED]: RunFinishedPayload
  [REALTIME_EVENT.INTERVENE_ACK]: { status?: string }
  [REALTIME_EVENT.INTERVENE_ERROR]: { code: string; message: string }
}

// ServerEvent 类型名（心跳除外）
export type ServerEventType =
  | (typeof REALTIME_EVENT)[keyof Omit<typeof REALTIME_EVENT, 'INTERVENE_ACK' | 'INTERVENE_ERROR'>]

// ServerEvent 联合（宽松 envelope：payload 按具体事件在订阅处收窄）
export type ServerEvent = {
  [K in keyof RealtimePayloadMap]: {
    v: string
    event_id: string
    ts: number
    run_id?: string
    stage?: string
    type: K
    payload: RealtimePayloadMap[K]
  }
}[keyof RealtimePayloadMap]

// 通用信封：解码层统一按此结构读取；订阅处按 type 把 payload 收窄到具体类型
export interface RealtimeEnvelope<P = unknown> {
  v: string
  event_id: string
  ts: number
  run_id?: string
  stage?: ResearchStageName
  type: string
  payload?: P
}

// ─── 客户端指令与 ACK（[前端详细设计 §9.5]，与 REST 介入通道等价）───

export type ClientCommandType = 'intervene' | 'cancel'

export interface ClientCommand {
  v: string
  type: ClientCommandType
  request_id: string
  payload: Record<string, unknown>
}

export interface InterveneAckEnvelope {
  v: string
  event_id?: string
  ts: number
  type: 'intervene.ack'
  request_id: string
  payload?: { status?: string }
}

export interface InterveneErrorEnvelope {
  v: string
  event_id?: string
  ts: number
  type: 'intervene.error'
  request_id: string
  payload?: { code: string; message: string }
}

// 指令应答（ACK 或业务拒绝）
export type CommandAck =
  | { ok: true; status?: string }
  | { ok: false; code: string; message: string }

// ─── 便捷类型再导出，业务侧常用 ───

export type {
  ConflictResponse,
  Credibility,
  EvidenceResponse,
  RunStatus,
  RunTier,
  SourceLevel,
  SourceType
}

// 终态事件类型：收到后服务端关闭连接，客户端不再重连（与后端 ws.py _TERMINAL_TYPES 对齐）
export const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  REALTIME_EVENT.RUN_FINISHED,
  REALTIME_EVENT.RUN_FAILED
])
