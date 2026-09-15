// 埋点模块（[前端详细设计 §16.1]，WP-18 实装）
// 统一 schema：{ event, ts, run_id?, page, props }，props 仅允许分类/标识字段，
// 禁止 token、问题原文等敏感内容。
// 上报策略：本地队列（localStorage 持久化，刷新/断网不丢）+ 达阈值立即批量上报 +
// 定时兜底 + 页面隐藏时 keepalive 补发。埋点是辅助通道：任何失败静默保留队列，
// 绝不阻塞主链路；队列超长丢最旧事件（丢批可接受，详设 §16.1）。
import { postTelemetry } from '@/services/api/telemetry'
import type { TelemetryEvent } from '@/services/api/types'

const QUEUE_STORAGE_KEY = 'telemetry_queue_v1'
// 达此条数立即触发批量上报
const FLUSH_THRESHOLD = 5
// 未满阈值时的定时兜底间隔
const FLUSH_INTERVAL_MS = 10_000
// 离线队列上限：超出丢最旧，避免 localStorage 无限膨胀
const MAX_QUEUE = 100

// props 取值域：字符串（分类/枚举/标识 id）、数值（耗时等）、布尔、缺省
export type TelemetryProps = Record<string, string | number | boolean | undefined>

let queue = loadQueue()
let flushing = false
let flushTimer: number | undefined
let listenersBound = false

function loadQueue(): TelemetryEvent[] {
  try {
    const raw = localStorage.getItem(QUEUE_STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // 只回收结构完整的事件，损坏项直接丢弃
    return parsed.filter(
      (item): item is TelemetryEvent =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as TelemetryEvent).event === 'string' &&
        typeof (item as TelemetryEvent).ts === 'number'
    )
  } catch {
    return []
  }
}

function persistQueue(): void {
  try {
    localStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(queue))
  } catch {
    // 存储不可用（隐私模式/配额）：仅内存保留，本次会话仍可上报
  }
}

// 单次入队；runId 进入事件顶层 run_id（schema 固定字段），不进 props
export function track(event: string, props?: TelemetryProps, runId?: string): void {
  const item: TelemetryEvent = {
    event,
    ts: Date.now(),
    page: window.location.pathname
  }
  if (runId) item.run_id = runId
  if (props && Object.keys(props).length > 0) item.props = props

  queue.push(item)
  if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE)
  persistQueue()

  if (import.meta.env.DEV) {
    // dev 预览沿用 warn 级别（lint 仅放行 warn/error），便于验收时在控制台核对事件
    console.warn('[telemetry]', event, props ?? {})
  }

  bindLifecycleListeners()
  if (queue.length >= FLUSH_THRESHOLD) {
    void flush()
  } else {
    ensureFlushTimer()
  }
}

function ensureFlushTimer(): void {
  if (flushTimer !== undefined) return
  flushTimer = window.setInterval(() => {
    void flush()
  }, FLUSH_INTERVAL_MS)
}

// 批量上报当前队列；在途时跳过（下一次触发补发新事件），失败整体保留
async function flush(keepalive = false): Promise<void> {
  if (flushing || queue.length === 0) return
  flushing = true
  const batch = queue.slice()
  try {
    await postTelemetry({ events: batch }, keepalive)
    // 仅移除本次在批事件；在途期间新入队事件保留
    queue = queue.slice(batch.length)
    persistQueue()
    if (queue.length === 0 && flushTimer !== undefined) {
      window.clearInterval(flushTimer)
      flushTimer = undefined
    }
  } catch {
    // 静默：真实端点未就绪/断网时不报错、不阻塞，等下次定时或阈值触发补报
  } finally {
    flushing = false
  }
}

// 页面隐藏（切后台/关页/导航）时用 keepalive 尽量把队列发出
function onVisibilityHidden(): void {
  if (document.visibilityState === 'hidden') void flush(true)
}

function bindLifecycleListeners(): void {
  if (listenersBound) return
  listenersBound = true
  document.addEventListener('visibilitychange', onVisibilityHidden)
  window.addEventListener('pagehide', onVisibilityHidden)
}
