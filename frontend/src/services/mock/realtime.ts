// WebSocket 频道管理：基于 ws 包实现 WS 服务端
// 与后端 app/realtime/ws.py 同构：/api/v1/ws/runs/{run_id}/stream?token=<access>，
// 30s 心跳；终态事件由路由层广播后调用 closeWsConnections 关闭连接

import type { WebSocket as WsWebSocket } from 'ws'
import {
  REALTIME_PROTOCOL_VERSION,
  type InterveneAckEnvelope,
  type InterveneErrorEnvelope,
  type RealtimeEnvelope,
  type RealtimePing,
  type RealtimePong
} from '../realtime/types'
import { store } from './store'

// WS 连接池：run_id -> 连接集合
const wsConnections = new Map<string, Set<WsWebSocket>>()

// 心跳定时器
const heartbeatTimers = new Map<WsWebSocket, NodeJS.Timeout>()

// 心跳间隔与后端一致（30 秒）
const HEARTBEAT_INTERVAL_MS = 30_000

// 客户端指令处理器：由 engine.ts 在模块加载时注入（intervene/cancel 与 REST 通道等价）
export interface WsCommandInput {
  type?: unknown
  request_id?: unknown
  payload?: unknown
}
// 可辨识联合：ACK 携带状态，错误携带 code/message，分别对齐两种帧的 payload 契约
export type WsCommandReply =
  | { type: 'intervene.ack'; requestId: string; payload: { status?: string } }
  | { type: 'intervene.error'; requestId: string; payload: { code: string; message: string } }
let commandHandler: ((runId: string, command: WsCommandInput) => WsCommandReply | null) | null = null

export function setCommandHandler(
  handler: (runId: string, command: WsCommandInput) => WsCommandReply | null
): void {
  commandHandler = handler
}

// 校验 query token：必须是登录后签发且仍有效的 mock access token
// 网关在 WS 握手前调用，无效时直接拒绝升级（对齐后端 accept 前 close 的语义）
export function isAccessTokenValid(token: string | null): boolean {
  return !!token && store.accessTokens.has(token)
}

// 处理已通过鉴权的 WS 连接
export function handleWsUpgrade(ws: WsWebSocket, runId: string): void {
  // 注册连接
  const connections = wsConnections.get(runId) ?? new Set<WsWebSocket>()
  connections.add(ws)
  wsConnections.set(runId, connections)

  startHeartbeat(ws)

  ws.on('message', (data) => {
    try {
      const msg: unknown = JSON.parse(data.toString())
      handleWsMessage(ws, runId, msg)
    } catch {
      // 忽略无法解析的客户端消息
    }
  })

  ws.on('close', () => {
    stopHeartbeat(ws)
    const set = wsConnections.get(runId)
    if (!set) return
    set.delete(ws)
    if (set.size === 0) {
      wsConnections.delete(runId)
    }
  })

  ws.on('error', () => {
    stopHeartbeat(ws)
  })
}

// 处理客户端消息：心跳应答 + intervene/cancel 指令（ACK/错误帧带 request_id 关联）
function handleWsMessage(ws: WsWebSocket, runId: string, msg: unknown): void {
  if (typeof msg !== 'object' || msg === null) return
  const type = (msg as { type?: unknown }).type
  if (type === 'pong') return

  if (type === 'ping') {
    const pong: RealtimePong = { v: REALTIME_PROTOCOL_VERSION, type: 'pong', ts: Date.now() }
    sendWsMessage(ws, pong)
    return
  }

  if (type === 'intervene' || type === 'cancel') {
    const reply = commandHandler?.(runId, msg as WsCommandInput)
    if (!reply || !reply.requestId) return
    const frame: InterveneAckEnvelope | InterveneErrorEnvelope =
      reply.type === 'intervene.ack'
        ? {
            v: REALTIME_PROTOCOL_VERSION,
            ts: Date.now(),
            type: 'intervene.ack',
            request_id: reply.requestId,
            payload: reply.payload
          }
        : {
            v: REALTIME_PROTOCOL_VERSION,
            ts: Date.now(),
            type: 'intervene.error',
            request_id: reply.requestId,
            payload: reply.payload
          }
    sendWsMessage(ws, frame)
  }
}

// 启动心跳：服务端定时发 ping
function startHeartbeat(ws: WsWebSocket): void {
  stopHeartbeat(ws)
  const timer = setInterval(() => {
    const ping: RealtimePing = { v: REALTIME_PROTOCOL_VERSION, type: 'ping', ts: Date.now() }
    sendWsMessage(ws, ping)
  }, HEARTBEAT_INTERVAL_MS)
  heartbeatTimers.set(ws, timer)
}

function stopHeartbeat(ws: WsWebSocket): void {
  const timer = heartbeatTimers.get(ws)
  if (timer) {
    clearInterval(timer)
    heartbeatTimers.delete(ws)
  }
}

// 向指定 run 的所有 WS 连接广播事件
export function broadcastWsEvent(runId: string, event: RealtimeEnvelope): void {
  const connections = wsConnections.get(runId)
  if (!connections) return
  for (const ws of connections) {
    sendWsMessage(ws, event)
  }
}

function sendWsMessage(
  ws: WsWebSocket,
  msg: RealtimePing | RealtimePong | RealtimeEnvelope | InterveneAckEnvelope | InterveneErrorEnvelope
): void {
  // readyState 1 = WebSocket.OPEN
  if (ws.readyState !== 1) return
  try {
    ws.send(JSON.stringify(msg))
  } catch {
    // 连接已关闭，发送失败时忽略
  }
}

// 关闭指定 run 的所有连接（终态事件广播后调用）
export function closeWsConnections(runId: string): void {
  const connections = wsConnections.get(runId)
  if (!connections) return
  for (const ws of connections) {
    stopHeartbeat(ws)
    try {
      ws.close(1000, 'Run finished')
    } catch {
      // 连接可能已关闭，忽略
    }
  }
  wsConnections.delete(runId)
}
