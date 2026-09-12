// Envelope 构造工具：与后端 realtime/ws.py 的 _make_envelope 同构
// 后端语义：payload = event.payload ?? event 整体；stage = event.stage ?? event.current_stage

// mock 网关在 vite.config 的 Node 上下文中加载，本文件必须使用相对路径，禁止 @/ 别名
import type { RealtimeEnvelope } from '../../realtime/types'
import { REALTIME_PROTOCOL_VERSION } from '../../realtime/types'
import type { ResearchStageName } from '../../api/types'
import { RESEARCH_STAGES } from '../../domain/stages'

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
  const stageName = event.stage ?? event.current_stage
  // 仅当取值为已知阶段时写入信封 stage，未知值交由订阅者按 payload 兜底
  const stage = RESEARCH_STAGES.includes(stageName as ResearchStageName)
    ? (stageName as ResearchStageName)
    : undefined
  return {
    v: REALTIME_PROTOCOL_VERSION,
    event_id: `evt_${event.type}_${ts}`,
    ts,
    run_id: runId,
    stage,
    type: event.type,
    payload: event.payload ?? event
  }
}
