// 运行看板数据 API（M2-4：LLD §4.2.4/§4.2.6 + §4.2.5 成本快照）
// 指挥舱实时看板的 REST 补齐数据源；高频增量走 WS，这些端点用于初帧与重连补齐。
import { http } from '../http/http'
import type {
  ConflictResponse,
  CostSnapshot,
  EvidenceListParams,
  EvidenceResponse,
  PageEnvelope,
  StageResponse,
  SubQuestionResponse
} from './types'

// GET /runs/{id}/stages：6 阶段执行记录
export function getRunStages(runId: string): Promise<StageResponse[]> {
  return http<StageResponse[]>(`/runs/${encodeURIComponent(runId)}/stages`)
}

// GET /runs/{id}/sub-questions：子问题列表与进度
export function getRunSubQuestions(runId: string): Promise<SubQuestionResponse[]> {
  return http<SubQuestionResponse[]>(`/runs/${encodeURIComponent(runId)}/sub-questions`)
}

// GET /runs/{id}/evidence：证据池分页（支持按子问题过滤、包含已剔除）
export function listRunEvidence(
  runId: string,
  params?: EvidenceListParams
): Promise<PageEnvelope<EvidenceResponse>> {
  const search = new URLSearchParams()
  if (params?.page !== undefined) search.set('page', String(params.page))
  if (params?.page_size !== undefined) search.set('page_size', String(params.page_size))
  if (params?.sub_question_id) search.set('sub_question_id', params.sub_question_id)
  if (params?.include_excluded) search.set('include_excluded', 'true')
  const query = search.toString()
  return http<PageEnvelope<EvidenceResponse>>(
    `/runs/${encodeURIComponent(runId)}/evidence${query ? `?${query}` : ''}`
  )
}

// GET /runs/{id}/conflicts：run 下分歧列表
export function getRunConflicts(runId: string): Promise<ConflictResponse[]> {
  return http<ConflictResponse[]>(`/runs/${encodeURIComponent(runId)}/conflicts`)
}

// GET /conflicts/{id}：分歧详情（含双方证据，M2 只读呈现用）
export function getConflict(conflictId: string): Promise<ConflictResponse> {
  return http<ConflictResponse>(`/conflicts/${encodeURIComponent(conflictId)}`)
}

// GET /runs/{id}/cost/snapshot：成本快照（WS 断连补齐用）
export function getCostSnapshot(runId: string): Promise<CostSnapshot> {
  return http<CostSnapshot>(`/runs/${encodeURIComponent(runId)}/cost/snapshot`)
}
