// 实时事件类型定义（[前端详细设计 §8.3 §9.2]，对齐后端 app/realtime/ws.py）
// M1 实时通道统一为 WebSocket：/api/v1/ws/runs/{run_id}/stream?token=<jwt>
// （后端 M1 采用 query 参数鉴权；SSE 通道已移除，M2+ 如恢复再在此扩展）

// 协议版本（与后端 _make_envelope 协商一致）
export const REALTIME_PROTOCOL_VERSION = '1.0' as const

// M1 事件类型（后端实际发布）
export const REALTIME_EVENT = {
  STAGE_STARTED: 'stage.started',
  RUN_FINISHED: 'run.finished',
  RUN_FAILED: 'run.failed'
} as const

// 终态事件：收到后服务端关闭连接，客户端应停止重连
export const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  REALTIME_EVENT.RUN_FINISHED,
  REALTIME_EVENT.RUN_FAILED
])

// 服务端推送 envelope（ws.py _make_envelope）
export interface RealtimeEnvelope<P = unknown> {
  v: string
  event_id: string
  ts: number
  run_id?: string
  stage?: string
  type: string
  payload: P
}

// 客户端发送指令（[§8.3 客户端消息模型]；M1 仅 pong，intervene/cancel 走 REST）
export interface RealtimeCommand<P = unknown> {
  v: string
  type: string
  request_id: string
  payload: P
}

// 心跳（裸 JSON；服务端 30s 发 ping，客户端须 60s 内回 pong）
export interface RealtimePing {
  v?: string
  type: 'ping'
  ts?: number
}

export interface RealtimePong {
  v: string
  type: 'pong'
  ts: number
}

// 连接状态机（[§9.3 状态机]）
export type ChannelState = 'idle' | 'connecting' | 'live' | 'retrying' | 'paused'

// ─── M1 事件 payload ───

// stage.started：阶段切换；stage 同时提升到 envelope 顶层
export interface StageStartedPayload {
  stage?: string
  attempt?: number
}

// run.finished / run.failed：终态事件（ws.py 将 event 整体放入 payload）
export interface RunFinishedPayload {
  status: 'succeeded' | 'failed' | 'cancelled' | 'paused'
  current_stage?: string | null
  token_used?: number
  error_code?: string
  error_message?: string
}
