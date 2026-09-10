// RealtimeClient 最小骨架（[前端详细设计 §8.3 §9 §4.2]）
// 一个 RealtimeClient 单例按 channelId 持有 WebSocket 连接
// 当前仅覆盖 WS 心跳/重连；SSE 通道、断线补齐（lastEventId 去重）由上层 useRunStream 接入时补
import {
  WS_AUTH_PROTOCOL,
  type ChannelState,
  type RealtimeEnvelope,
  type RealtimePing,
  type RealtimePong
} from './types'

// 通道配置
export interface ChannelConfig {
  url: string
  token?: string
  // 心跳：服务端每 30s ping（[§8.3]）；客户端兜底主动 ping 避免反向超时
  heartbeatIntervalMs?: number
  // 重连：指数退避 [§9.3]
  maxBackoffMs?: number
}

// 事件订阅（type -> 处理器集合）
type Handler = (env: RealtimeEnvelope) => void

// 单个 WS 通道
export class WsChannel {
  readonly id: string
  private readonly url: string
  private readonly token: string | undefined
  private readonly heartbeatIntervalMs: number
  private readonly maxBackoffMs: number

  private ws: WebSocket | null = null
  private state: ChannelState = 'idle'
  private handlers: Map<string, Set<Handler>> = new Map()
  private anyHandler: Set<Handler> = new Set()
  private lastEventId: string = ''
  private backoffMs = 1000
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private intentionalClose = false
  private onStateChange: ((state: ChannelState) => void) | null = null

  constructor(id: string, config: ChannelConfig) {
    this.id = id
    this.url = config.url
    this.token = config.token
    this.heartbeatIntervalMs = config.heartbeatIntervalMs ?? 25000
    this.maxBackoffMs = config.maxBackoffMs ?? 30000
  }

  getState(): ChannelState {
    return this.state
  }

  getLastEventId(): string {
    return this.lastEventId
  }

  setOnStateChange(cb: (state: ChannelState) => void): void {
    this.onStateChange = cb
  }

  private setState(s: ChannelState): void {
    if (this.state === s) return
    this.state = s
    this.onStateChange?.(s)
  }

  // 订阅某 type 事件；返回取消函数
  on(type: string, handler: Handler): () => void {
    let set = this.handlers.get(type)
    if (!set) {
      set = new Set()
      this.handlers.set(type, set)
    }
    set.add(handler)
    return () => set!.delete(handler)
  }

  // 订阅所有事件（兜底；用于路由+埋点）
  onAny(handler: Handler): () => void {
    this.anyHandler.add(handler)
    return () => this.anyHandler.delete(handler)
  }

  // 客户端发送指令（[§8.3]）
  send(payload: object): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false
    try {
      this.ws.send(JSON.stringify(payload))
      return true
    } catch {
      return false
    }
  }

  // 建立连接
  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    this.intentionalClose = false
    this.setState('connecting')
    try {
      const protocols = this.token ? [WS_AUTH_PROTOCOL, `${WS_AUTH_PROTOCOL}.${this.token}`] : undefined
      this.ws = protocols ? new WebSocket(this.url, protocols) : new WebSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws.onopen = () => {
      this.backoffMs = 1000
      this.setState('live')
      this.startHeartbeat()
    }
    this.ws.onmessage = (ev) => {
      this.handleMessage(typeof ev.data === 'string' ? ev.data : '')
    }
    this.ws.onerror = () => {
      // error 紧跟 close；不直接重连，由 close 统一处理
    }
    this.ws.onclose = () => {
      this.stopHeartbeat()
      if (this.intentionalClose) {
        this.setState('idle')
        return
      }
      this.scheduleReconnect()
    }
  }

  // 关闭连接（[§9.3 延迟关闭由上层实现]）
  disconnect(): void {
    this.intentionalClose = true
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopHeartbeat()
    if (this.ws) {
      try {
        this.ws.close(1000, 'client_close')
      } catch {
        /* noop */
      }
      this.ws = null
    }
    this.setState('idle')
  }

  // 暂停（保留连接，仅停止事件处理）
  pause(): void {
    if (this.state === 'live') this.setState('paused')
  }

  // 恢复
  resume(): void {
    if (this.state === 'paused') this.setState('live')
  }

  // ─── 内部 ───
  private handleMessage(raw: string): void {
    if (!raw) return
    let msg: { type?: string; ts?: number; event_id?: string }
    try {
      msg = JSON.parse(raw) as { type?: string; ts?: number; event_id?: string }
    } catch {
      return
    }
    // 心跳：服务端 ping 客户端回 pong（裸 JSON，无 envelope）
    if (msg.type === 'ping') {
      const pong: RealtimePong = { type: 'pong', ts: Date.now() }
      this.send(pong as unknown as object)
      return
    }
    if (msg.type === 'pong') return

    // envelope 事件
    if (msg.event_id) {
      const env = msg as unknown as RealtimeEnvelope
      this.lastEventId = env.event_id
      const set = this.handlers.get(env.type)
      if (set) for (const h of set) {
        try { h(env) } catch { /* swallow handler error to keep channel alive */ }
      }
      for (const h of this.anyHandler) {
        try { h(env) } catch { /* same */ }
      }
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => {
      const ping: RealtimePing = { type: 'ping', ts: Date.now() }
      this.send(ping as unknown as object)
    }, this.heartbeatIntervalMs)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  private scheduleReconnect(): void {
    this.setState('retrying')
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    // 指数退避 + 抖动（±20%）
    const jitter = this.backoffMs * (0.8 + Math.random() * 0.4)
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs)
      this.connect()
    }, jitter)
  }
}

// RealtimeClient 单例（[§9.1]）
class RealtimeClientImpl {
  private channels: Map<string, WsChannel> = new Map()

  // 获取或创建频道
  ensureChannel(id: string, config: ChannelConfig): WsChannel {
    let ch = this.channels.get(id)
    if (!ch) {
      ch = new WsChannel(id, config)
      this.channels.set(id, ch)
    }
    return ch
  }

  getChannel(id: string): WsChannel | undefined {
    return this.channels.get(id)
  }

  // 移除频道（断开并释放）
  destroyChannel(id: string): void {
    const ch = this.channels.get(id)
    if (!ch) return
    ch.disconnect()
    this.channels.delete(id)
  }

  // 销毁所有（应用卸载时）
  destroyAll(): void {
    for (const [id] of this.channels) this.destroyChannel(id)
  }
}

export const realtimeClient = new RealtimeClientImpl()
