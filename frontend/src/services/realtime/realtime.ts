// RealtimeClient（[前端详细设计 §8.3 §9]，对齐后端 app/realtime/ws.py）
// 一个 RealtimeClient 单例按 channelId 持有 WebSocket 连接；
// 覆盖 WS 心跳、指数退避重连、终态事件识别；断线补齐（lastEventId 去重）由上层 useRunStream 接入时补。
// 鉴权：token 已由 services/api buildRunStreamUrl 拼入 URL query（?token=<jwt>）。
import {
  REALTIME_PROTOCOL_VERSION,
  TERMINAL_EVENT_TYPES,
  type ChannelState,
  type RealtimeEnvelope,
  type RealtimePing,
  type RealtimePong
} from './types'

// 通道配置
export interface ChannelConfig {
  // 完整 WS 地址（含 ?token= 查询参数）
  url: string
  // 心跳：服务端每 30s ping（[§8.3]）；客户端兜底主动 ping 避免反向超时
  heartbeatIntervalMs?: number
  // 重连：指数退避 [§9.3]
  maxBackoffMs?: number
  // 握手超时：超时未收到 onopen 则主动关闭并转入退避重连
  // （代理异常时浏览器可能既不 open 也不 close，必须由客户端打破静默）
  connectTimeoutMs?: number
}

// 事件订阅（type -> 处理器集合）
type Handler = (env: RealtimeEnvelope) => void

// 单个 WS 通道
export class WsChannel {
  readonly id: string
  private readonly url: string
  private readonly heartbeatIntervalMs: number
  private readonly maxBackoffMs: number
  private readonly connectTimeoutMs: number

  private ws: WebSocket | null = null
  private state: ChannelState = 'idle'
  private handlers: Map<string, Set<Handler>> = new Map()
  private anyHandler: Set<Handler> = new Set()
  private lastEventId: string = ''
  private backoffMs = 1000
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private connectTimer: number | null = null
  private intentionalClose = false
  // 已收到终态事件（run.finished/run.failed）：服务端随后关连接，不再重连
  private terminalReceived = false
  private onStateChange: ((state: ChannelState) => void) | null = null

  constructor(id: string, config: ChannelConfig) {
    this.id = id
    this.url = config.url
    this.heartbeatIntervalMs = config.heartbeatIntervalMs ?? 25000
    this.maxBackoffMs = config.maxBackoffMs ?? 30000
    this.connectTimeoutMs = config.connectTimeoutMs ?? 10000
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
    this.terminalReceived = false
    this.setState('connecting')
    try {
      // 鉴权 token 已包含在 url query 中，无需 Sec-WebSocket-Protocol
      this.ws = new WebSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.startConnectTimeout()
    this.ws.onopen = () => {
      this.stopConnectTimeout()
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
      this.stopConnectTimeout()
      this.stopHeartbeat()
      if (this.intentionalClose || this.terminalReceived) {
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
    this.stopConnectTimeout()
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
      const pong: RealtimePong = { v: REALTIME_PROTOCOL_VERSION, type: 'pong', ts: Date.now() }
      this.send(pong as unknown as object)
      return
    }
    if (msg.type === 'pong') return

    // envelope 事件
    if (msg.event_id) {
      const env = msg as unknown as RealtimeEnvelope
      this.lastEventId = env.event_id
      // 终态事件：标记后不再重连，服务端会主动关闭连接
      if (TERMINAL_EVENT_TYPES.has(env.type)) {
        this.terminalReceived = true
      }
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
      const ping: RealtimePing = { v: REALTIME_PROTOCOL_VERSION, type: 'ping', ts: Date.now() }
      this.send(ping as unknown as object)
    }, this.heartbeatIntervalMs)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  private startConnectTimeout(): void {
    this.stopConnectTimeout()
    this.connectTimer = window.setTimeout(() => {
      this.connectTimer = null
      const ws = this.ws
      if (!ws) return
      // 握手超时：摘除回调避免主动 close 触发 onclose 重复调度重连
      this.ws = null
      ws.onopen = null
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
      try {
        ws.close()
      } catch {
        /* noop */
      }
      this.stopHeartbeat()
      this.scheduleReconnect()
    }, this.connectTimeoutMs)
  }

  private stopConnectTimeout(): void {
    if (this.connectTimer !== null) {
      window.clearTimeout(this.connectTimer)
      this.connectTimer = null
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
