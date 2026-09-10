// SSE 流管理（[前端M0收尾方案 §4.3]）
// 直接走 Node http.ServerResponse 写入 text/event-stream

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { RealtimeEnvelope } from '../realtime/types'

// SSE 连接池
const sseConnections = new Map<string, Set<ServerResponse>>()

// 创建 SSE 连接
export function createSseConnection(
  req: IncomingMessage,
  res: ServerResponse,
  runId: string
): void {
  // 设置 SSE 响应头
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*'
  })

  // 注册连接
  if (!sseConnections.has(runId)) {
    sseConnections.set(runId, new Set())
  }
  sseConnections.get(runId)!.add(res)

  // 发送初始连接确认
  sendSseEvent(res, { type: 'connected', payload: { runId } } as any)

  // 客户端断开时清理
  req.on('close', () => {
    const connections = sseConnections.get(runId)
    if (connections) {
      connections.delete(res)
      if (connections.size === 0) {
        sseConnections.delete(runId)
      }
    }
  })
}

// 向指定 run 的所有 SSE 连接发送事件
export function broadcastSseEvent(runId: string, event: RealtimeEnvelope): void {
  const connections = sseConnections.get(runId)
  if (!connections) return

  for (const res of connections) {
    sendSseEvent(res, event)
  }
}

// 发送单个 SSE 事件
function sendSseEvent(res: ServerResponse, event: any): void {
  const data = `data: ${JSON.stringify(event)}\n\n`
  try {
    res.write(data)
  } catch {
    // 连接已关闭，忽略错误
  }
}

// 关闭指定 run 的所有 SSE 连接
export function closeSseConnections(runId: string): void {
  const connections = sseConnections.get(runId)
  if (!connections) return

  for (const res of connections) {
    try {
      res.end()
    } catch {
      // 忽略
    }
  }
  sseConnections.delete(runId)
}
