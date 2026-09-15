// RealtimeClient（[前端详细设计 §8.3 §9.1 §9.3 §9.5]）
// 覆盖：连接生命周期、心跳、握手超时、指数退避重连、event_id 幂等去重、
//       跨页面频道引用计数（无订阅者延迟 5s 断开）、客户端指令 request_id/ACK 关联。
// 鉴权：M2 传输层支持 query（M1 现状）与子协议（契约草案 §5.3，随后端窗口切换）。
import {
  REALTIME_PROTOCOL_VERSION,
  TERMINAL_EVENT_TYPES,
  type ChannelState,
  type ClientCommand,
  type ClientCommandType,
  type CommandAck,
  type InterveneAckEnvelope,
  type InterveneErrorEnvelope,
  type RealtimeEnvelope,
  type RealtimePing,
  type RealtimePong
} from './types'

// 通道配置
export interface ChannelConfig {
  url: string
  // 完整 WS 地址（含 ?token= 查询参数，authMode=query 时使用）
  // 客户端兜底心跳间隔（默认 25s 主动 ping；另对服务端 30s ping 回 pong）
  clientHeartbeatIntervalMs?: number
  // 握手超时：超时未收到 onopen 则主动关闭并转入重连
  connectTimeoutMs?: number
  // 重连：指数退避 [1000,30000]ms + ±20% 抖动
  maxBackoffMs?: number
  // 鉴权注入方式：query=M1 现状（URL 带 token）；subprotocol=Sec-WebSocket-Protocol（M2 与后端同窗口切换）
  authMode?: 'query' | 'subprotocol'
  // subprotocol 模式使用的协议名（契约草案 §5.3：bearer，握手头为 Sec-WebSocket-Protocol: bearer, <jwt>）
  authProtocol?: string
}

// 事件订阅（心跳除外）：返回取消函数
export type EventHandler = (env: RealtimeEnvelope) => void

interface PendingCommand {
  resolve: (ack: Extract<CommandAck, { ok: true }>) => void
  reject: (err: Error) => void
  timer: ReturnType<typeof setTimeout>
}

// 指令 ACK 等待上限（服务端应即时应答；超时按通道异常处理）
const COMMAND_ACK_TIMEOUT_MS = 10_000
// 去重集合容量上限（每频道 FIFO 2000，§9.3）
const MAX_SEEN_EVENT_IDS = 2000
// 最后一个订阅者释放后延迟断开时长（跨页面切换不重建连接，§7.3）
const CHANNEL_RELEASE_DELAY_MS = 5_000

// 单条 WS 通道
export class WsChannel {
  readonly id: string
  private readonly url: string
  private readonly authMode: 'query' | 'subprotocol'
  private readonly authProtocol: string
  private readonly clientHeartbeatIntervalMs: number
  private readonly connectTimeoutMs: number
  private readonly maxBackoffMs: number

  private ws: WebSocket | null = null
  private state: ChannelState = 'idle'
  private backoffMs = 1000

  // type -> 处理器集合
  private handlers: Map<string, Set<EventHandler>> = new Map()
  private anyHandler: Set<(type: string, env: RealtimeEnvelope) => void> = new Set()

  // event_id 幂等去重（FIFO）
  private seenEventIds: Set<string> = new Set()
  private seenEventOrder: string[] = []

  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private connectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false
  // 已收到终态事件：服务端随后关连接，不再重连
  private terminalReceived = false

  // 指令 ACK 等待表：request_id -> pending
  private pendingCommands: Map<string, PendingCommand> = new Map()

  // 状态变化回调（RealtimeClient 引用计数层使用）
  private onStateChange: ((state: ChannelState) => void) | null = null

  constructor(id: string, config: ChannelConfig) {
    this.id = id
    this.url = config.url
    this.authMode = config.authMode ?? 'query'
    this.authProtocol = config.authProtocol ?? 'bearer'
    // 客户端兜底心跳 25s（[§8.3]，另响应服务端 30s ping 回 pong）
    this.clientHeartbeatIntervalMs = config.clientHeartbeatIntervalMs ?? 25_000
    this.connectTimeoutMs = config.connectTimeoutMs ?? 10_000
    this.maxBackoffMs = config.maxBackoffMs ?? 30_000
  }

  getState(): ChannelState {
    return this.state
  }

  setOnStateChange(cb: ((state: ChannelState) => void) | null): void {
    this.onStateChange = cb
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
      // 鉴权两态：query 模式 token 在 URL；subprotocol 模式 URL 不带 token，
      // token 作为第二协议项传递（后端切换窗口前默认 query，不产生线上影响）
      this.ws =
        this.authMode === 'subprotocol'
          ? new WebSocket(this.stripToken(this.url), [this.authProtocol, this.extractToken(this.url)])
          : new WebSocket(this.url)
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
    this.ws.onmessage = (ev) => this.handleMessage(typeof ev.data === 'string' ? ev.data : '')
    this.ws.onerror = () => {
      // error 紧跟 close；不直接重连，由 onclose 统一处理
    }
    this.ws.onclose = () => {
      this.stopConnectTimeout()
      this.stopHeartbeat()
      this.failAllPending('实时连接已关闭')
      if (this.intentionalClose || this.terminalReceived) {
        this.setState('idle')
        return
      }
      this.scheduleReconnect()
    }
  }

  // 关闭连接（M1 语义：摘除回调后主动关闭；幂等）
  disconnect(): void {
    this.intentionalClose = true
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopConnectTimeout()
    this.stopHeartbeat()
    this.failAllPending('连接已断开')
    if (this.ws) {
      const ws = this.ws
      this.ws = null
      // 摘除回调避免主动 close 触发重连
      ws.onopen = null
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
      try {
        ws.close()
      } catch {
        /* noop */
      }
    }
    this.setState('idle')
  }

  // 订阅某 type 事件；返回取消函数
  on(type: string, handler: EventHandler): () => void {
    const set = this.handlers.get(type) ?? new Set<EventHandler>()
    set.add(handler)
    this.handlers.set(type, set)
    return () => set.delete(handler)
  }

  // 订阅所有事件（路由总线/埋点用）
  onAny(handler: (type: string, env: RealtimeEnvelope) => void): () => void {
    this.anyHandler.add(handler)
    return () => this.anyHandler.delete(handler)
  }

  // 发送客户端指令并等待 ACK（§9.5）；REST 等价路径见 services/api/interventions.ts
  sendCommand(type: ClientCommandType, payload: Record<string, unknown>, timeoutMs = COMMAND_ACK_TIMEOUT_MS): Promise<CommandAck> {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('CHANNEL_NOT_OPEN'))
        return
      }
      const requestId = createRequestId()
      const command: ClientCommand = {
        v: REALTIME_PROTOCOL_VERSION,
        type,
        request_id: requestId,
        payload
      }
      const timer = setTimeout(() => {
        this.pendingCommands.delete(requestId)
        reject(new Error('COMMAND_ACK_TIMEOUT'))
      }, timeoutMs)
      this.pendingCommands.set(requestId, {
        resolve: (ack) => resolve(ack),
        reject: (err) => reject(err),
        timer
      })
      try {
        this.ws.send(JSON.stringify(command))
      } catch (err) {
        clearTimeout(timer)
        this.pendingCommands.delete(requestId)
        reject(err instanceof Error ? err : new Error('COMMAND_SEND_FAILED'))
      }
    })
  }

  // ─── 内部 ───

  // 握手超时：未 onopen 时主动关连并转重连（摘除回调，避免走 onclose 重复调度）
  private startConnectTimeout(): void {
    this.stopConnectTimeout()
    this.connectTimer = setTimeout(() => {
      if (this.state !== 'connecting' || !this.ws) return
      const ws = this.ws
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
      clearTimeout(this.connectTimer)
      this.connectTimer = null
    }
  }

  // 服务端 ping/pong（兜底：M1 后端发裸 JSON ping，非 WebSocket 协议帧）
  private handleMessage(raw: string): void {
    if (!raw) return
    let msg: unknown
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }
    const type = (msg as { type?: unknown }).type
    if (type === 'ping') {
      const pong: RealtimePong = { v: REALTIME_PROTOCOL_VERSION, type: 'pong', ts: Date.now() }
      this.sendRaw(pong)
      return
    }
    if (type === 'pong') return

    // 指令 ACK/错误帧（带 request_id，无 run 维度）
    if (type === 'intervene.ack' || type === 'intervene.error') {
      this.resolveCommand(msg as InterveneAckEnvelope | InterveneErrorEnvelope)
      return
    }

    const env = msg as RealtimeEnvelope
    if (!env.type) return

    // 终态标记先于分发：服务端即将关连接，onclose 时不再重连
    if (TERMINAL_EVENT_TYPES.has(env.type)) {
      this.terminalReceived = true
    }

    // event_id 幂等去重（心跳/ACK 无 event_id，已在前面返回）
    if (env.event_id && this.isDuplicate(env.event_id)) return

    this.anyHandler.forEach((h) => h(env.type, env))
    const set = this.handlers.get(env.type)
    if (set) for (const h of set) h(env)
  }

  private resolveCommand(msg: InterveneAckEnvelope | InterveneErrorEnvelope): void {
    const pending = this.pendingCommands.get(msg.request_id)
    if (!pending) return
    this.pendingCommands.delete(msg.request_id)
    clearTimeout(pending.timer)
    if (msg.type === 'intervene.ack') {
      pending.resolve({ ok: true, status: msg.payload?.status })
    } else {
      const code = msg.payload?.code ?? 'INTERVENE_REJECTED'
      const message = msg.payload?.message ?? '操作被拒绝'
      pending.reject(Object.assign(new Error(message), { code }))
    }
  }

  private failAllPending(message: string): void {
    for (const [id, pending] of this.pendingCommands) {
      clearTimeout(pending.timer)
      pending.reject(new Error(message))
      this.pendingCommands.delete(id)
    }
  }

  // 返回 true 表示重复事件，应丢弃
  private isDuplicate(eventId: string): boolean {
    if (this.seenEventIds.has(eventId)) return true
    this.seenEventIds.add(eventId)
    this.seenEventOrder.push(eventId)
    if (this.seenEventOrder.length > MAX_SEEN_EVENT_IDS) {
      const oldest = this.seenEventOrder.shift()
      if (oldest) this.seenEventIds.delete(oldest)
    }
    return false
  }

  // 测试/重连补齐需要：重置去重记忆（重连后服务端不回放，历史帧不会重发）
  resetSeenEvents(): void {
    this.seenEventIds.clear()
    this.seenEventOrder = []
  }

  // 客户端兜底心跳（[§8.3]：25s 主动 ping，防止反向超时；服务端 ping 在消息层回 pong）
  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      const ping: RealtimePing = { v: REALTIME_PROTOCOL_VERSION, type: 'ping', ts: Date.now() }
      this.sendRaw(ping)
    }, this.clientHeartbeatIntervalMs)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  private sendRaw(payload: RealtimePing | RealtimePong): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    try {
      this.ws.send(JSON.stringify(payload))
    } catch {
      /* noop */
    }
  }

  private scheduleReconnect(): void {
    this.stopHeartbeat()
    this.setState('retrying')
    if (this.reconnectTimer !== null) return
    const jitter = 0.8 + Math.random() * 0.4 // ±20%
    const delay = Math.min(this.backoffMs * jitter, this.maxBackoffMs)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs)
      this.connect()
    }, delay)
  }

  private setState(s: ChannelState): void {
    if (this.state === s) return
    this.state = s
    this.onStateChange?.(s)
  }

  private stripToken(url: string): string {
    const u = new URL(url)
    u.searchParams.delete('token')
    return u.toString()
  }

  private extractToken(url: string): string {
    return new URL(url).searchParams.get('token') ?? ''
  }
}

function createRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `req_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

// ─── RealtimeClient 单例：按 channelId 持有频道，引用计数共享 ───

interface ChannelEntry {
  channel: WsChannel
  refs: number
  releaseTimer: ReturnType<typeof setTimeout> | null
}

class RealtimeClientImpl {
  private channels: Map<string, ChannelEntry> = new Map()

  // 获取或创建频道并增加一个引用（同一 run 跨页面共享一条连接）
  ensureChannel(id: string, config: ChannelConfig): WsChannel {
    let entry = this.channels.get(id)
    if (!entry) {
      const channel = new WsChannel(id, config)
      entry = { channel, refs: 0, releaseTimer: null }
      this.channels.set(id, entry)
    }
    if (entry.releaseTimer !== null) {
      clearTimeout(entry.releaseTimer)
      entry.releaseTimer = null
    }
    entry.refs += 1
    return entry.channel
  }

  // 释放一个引用；归零后延迟断开（期间再次 ensure 可复用）
  releaseChannel(id: string): void {
    const entry = this.channels.get(id)
    if (!entry) return
    entry.refs = Math.max(0, entry.refs - 1)
    if (entry.refs > 0) return
    if (entry.releaseTimer !== null) return
    entry.releaseTimer = setTimeout(() => {
      const current = this.channels.get(id)
      if (!current || current.refs > 0) {
        if (current) current.releaseTimer = null
        return
      }
      current.channel.disconnect()
      this.channels.delete(id)
    }, CHANNEL_RELEASE_DELAY_MS)
  }

  getChannel(id: string): WsChannel | undefined {
    return this.channels.get(id)?.channel
  }

  // 终态/异常时立即销毁频道（不等待引用释放）
  destroyChannel(id: string): void {
    const entry = this.channels.get(id)
    if (!entry) return
    if (entry.releaseTimer !== null) clearTimeout(entry.releaseTimer)
    entry.channel.disconnect()
    this.channels.delete(id)
  }

  destroyAll(): void {
    for (const [id] of this.channels) {
      this.destroyChannel(id)
    }
  }
}

export const realtimeClient = new RealtimeClientImpl()
