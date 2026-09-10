// Envelope 构造工具：与后端 realtime/ws.py 的 _make_envelope 同构
// 后端语义：payload = event.payload ?? event 整体；stage = event.stage ?? event.current_stage

import type { RealtimeEnvelope } from '../../realtime/types'
import { REALTIME_PROTOCOL_VERSION } from '../../realtime/types'

// 后端发布的原始事件（未包 envelope）
export interface RawEvent {
  type: string
  run_id?: string
  stage?: string
  current_stage?: string
  payload?: unknown
  [key: string]: unknown
}

export function wrapEnvelope(runId: string, event: RawEvent): RealtimeEnvelope {
  const ts = Date.now()
  return {
    v: REALTIME_PROTOCOL_VERSION,
    event_id: `evt_${event.type}_${ts}`,
    ts,
    run_id: runId,
    stage: event.stage ?? event.current_stage,
    type: event.type,
    payload: event.payload ?? event
  }
}
