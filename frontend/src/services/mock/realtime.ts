// WebSocket 频道管理（[前端M0收尾方案 §4.3]）
// 基于 ws 包实现 WS 服务端

import type { WebSocket as WsWebSocket } from 'ws'
import type { IncomingMessage } from 'node:http'
import type { RealtimeEnvelope, RealtimePing, RealtimePong } from '../realtime/types'
import { WS_AUTH_PROTOCOL } from '../realtime/types'

// WS 连接池
const wsConnections = new Map<string, Set<WsWebSocket>>()

// 心跳定时器
const heartbeatTimers = new Map<WsWebSocket, NodeJS.Timeout>()

// 处理 WS 升级请求
export function handleWsUpgrade(
  ws: WsWebSocket,
  req: IncomingMessage,
  runId: string
): void {
  // 验证协议（可选）
  const protocol = req.headers['sec-websocket-protocol']
  if (protocol && !protocol.includes(WS_AUTH_PROTOCOL)) {
    ws.close(1008, 'Invalid protocol')
    return
  }

  // 注册连接
  if (!wsConnections.has(runId)) {
    wsConnections.set(runId, new Set())
  }
  wsConnections.get(runId)!.add(ws)

  // 启动心跳
  startHeartbeat(ws)

  // 消息处理
  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString())
      handleWsMessage(ws, runId, msg)
    } catch {
      // 忽略无效消息
    }
  })

  // 连接关闭
  ws.on('close', () => {
    stopHeartbeat(ws)
    const connections = wsConnections.get(runId)
    if (connections) {
      connections.delete(ws)
      if (connections.size === 0) {
        wsConnections.delete(runId)
      }
    }
  })

  // 错误处理
  ws.on('error', () => {
    stopHeartbeat(ws)
  })
}

// 处理客户端消息
function handleWsMessage(ws: WsWebSocket, _runId: string, msg: any): void {
  // 心跳：收到 ping 回复 pong
  if (msg.type === 'ping') {
    const pong: RealtimePong = { type: 'pong', ts: Date.now() }
    sendWsMessage(ws, pong as any)
    return
  }

  // 收到 pong 不处理
  if (msg.type === 'pong') return

  // 其他消息：按 envelope 处理（当前仅记录）
  if (msg.event_id) {
    // 可扩展：处理客户端指令（如 interrupt.respond）
  }
}

// 启动心跳
function startHeartbeat(ws: WsWebSocket): void {
  stopHeartbeat(ws)
  const timer = setInterval(() => {
    const ping: RealtimePing = { type: 'ping', ts: Date.now() }
    sendWsMessage(ws, ping as any)
  }, 25000) // 25s 心跳
  heartbeatTimers.set(ws, timer)
}

// 停止心跳
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

// 发送单个 WS 消息
function sendWsMessage(ws: WsWebSocket, msg: any): void {
  if (ws.readyState === 1) { // WebSocket.OPEN
    try {
      ws.send(JSON.stringify(msg))
    } catch {
      // 连接已关闭，忽略错误
    }
  }
}

// 关闭指定 run 的所有 WS 连接
export function closeWsConnections(runId: string): void {
  const connections = wsConnections.get(runId)
  if (!connections) return

  for (const ws of connections) {
    stopHeartbeat(ws)
    try {
      ws.close(1000, 'Run completed')
    } catch {
      // 忽略
    }
  }
  wsConnections.delete(runId)
}
