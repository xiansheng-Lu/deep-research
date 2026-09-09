// 实时事件类型与 envelope 定义（[前端详细设计 §9.2]）
// 与后端详细设计 §6.5.x 对照维护；版本 v1.0

export interface RealtimeEnvelope<T = unknown> {
  v: '1.0'
  event_id: string
  ts: number
  run_id: string
  stage?: string
  type: string
  payload: T
}

// 服务端 → 客户端 事件类型联合；联调时按后端实际发布补充
export type ServerEvent =
  | { type: 'stage.started'; payload: { stage: string; attempt?: number } }
  | { type: 'stage.finished'; payload: { stage: string } }
  | { type: 'stage.failed'; payload: { stage: string; reason: string; recoverable?: boolean } }
  | { type: 'sub_question.created'; payload: { sub_question_id: string; text: string } }
  | { type: 'sub_question.started'; payload: { sub_question_id: string } }
  | { type: 'sub_question.finished'; payload: { sub_question_id: string } }
  | { type: 'evidence.fetched'; payload: { evidence_id: string; snippet: string } }
  | { type: 'interrupt.requested'; payload: { reason: string; questions: unknown[] } }
  | { type: 'conflict.detected'; payload: { conflict_id: string; severity: string } }
  | { type: 'token.usage.update'; payload: { used: number; budget: number } }
  | { type: 'cost.warning'; payload: { level: string; used: number; budget: number; ratio: number } }
  | { type: 'report.chunk'; payload: { delta: string; position: number } }
  | { type: 'report.finished'; payload: { report_id: string } }
  | { type: 'run.finished'; payload: { status: 'succeeded' | 'failed' | 'cancelled' } }
