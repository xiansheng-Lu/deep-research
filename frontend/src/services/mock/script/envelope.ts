// Envelope 构造工具（[前端M0收尾方案 §4.3]）
// 与生产环境同构的 envelope 格式

import type { RealtimeEnvelope } from '../../realtime/types'
import { REALTIME_PROTOCOL_VERSION } from '../../realtime/types'

let eventIdCounter = 0

export function createEnvelope<T>(
  type: string,
  payload: T,
  options: {
    runId?: string
    stage?: string
  } = {}
): RealtimeEnvelope<T> {
  eventIdCounter += 1
  return {
    v: REALTIME_PROTOCOL_VERSION,
    event_id: `evt-${Date.now()}-${eventIdCounter}`,
    ts: Date.now(),
    run_id: options.runId,
    stage: options.stage,
    type,
    payload
  }
}

// 重置计数器（测试用）
export function resetEventIdCounter(): void {
  eventIdCounter = 0
}
