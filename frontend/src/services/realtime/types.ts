// 实时事件类型定义（[前端详细设计 §2.2 §8.3 §9.2]）
// envelope 协议：服务端推送与客户端发送共用同一组字段
// 此处只声明前端消费所需的最小结构；payload 形态以后端详细设计 §6.5 实际发布为准

// 协议版本（与后端协商一致）
export const REALTIME_PROTOCOL_VERSION = '1.0' as const

// 服务端推送 envelope
export interface RealtimeEnvelope<P = unknown> {
  v: string
  event_id: string
  ts: number
  run_id?: string
  stage?: string
  type: string
  payload: P
}

// 客户端发送指令（[§8.3 客户端消息模型]）
export interface RealtimeCommand<P = unknown> {
  v: string
  type: string
  request_id: string
  payload: P
}

// 心跳（裸 JSON，无 envelope；[§8.3 心跳]）
export interface RealtimePing {
  type: 'ping'
  ts: number
}

export interface RealtimePong {
  type: 'pong'
  ts: number
}

// WS 鉴权子协议（[§14.2 WS 客户端]）
export const WS_AUTH_PROTOCOL = 'bearer.jwt.v1' as const

// 连接状态机（[§9.3 状态机]）
export type ChannelState = 'idle' | 'connecting' | 'live' | 'retrying' | 'paused'

// 订阅类型（[§9.2 事件路由表]）
// MVP 仅声明核心事件；M1+ 视联调补齐
export interface EvidenceEvent {
  evidence_id: string
  snippet?: string
  // 完整字段以 §6.5.x 为准
}

export interface StageEvent {
  stage: string
  status: 'started' | 'succeeded' | 'failed' | 'skipped'
  attempt?: number
}

export interface TokenUsageEvent {
  used: number
  budget: number
  ratio: number
}

export interface CostWarningEvent {
  level: '70' | '90'
  used: number
  budget: number
  ratio: number
}

export interface InterruptRequestEvent {
  reason: string
  questions: Array<{
    id: string
    text: string
    type: 'single' | 'multi' | 'text'
    options?: Array<{ key: string; label: string; recommended?: boolean }>
  }>
  expires_in_seconds?: number
}

export interface ConflictDetectedEvent {
  conflict_id: string
  claim_a: string
  claim_b: string
  severity: 'low' | 'medium' | 'high'
}

export interface ReportChunkEvent {
  block_id: string
  delta: string
  done?: boolean
}

export interface ReportFinishedEvent {
  report_id: string
  total_blocks: number
}

export interface RunFinishedEvent {
  run_id: string
  status: 'succeeded' | 'failed' | 'cancelled'
}

export interface AckEvent {
  request_id: string
  ok: boolean
  code?: string
  message?: string
}
