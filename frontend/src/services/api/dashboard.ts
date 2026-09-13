// 运行看板数据 API（M2-4：LLD §4.2.4/§4.2.6 + §4.2.5 成本快照）
// 指挥舱实时看板的 REST 补齐数据源；高频增量走 WS，这些端点用于初帧与重连补齐。
import { http } from '../http/http'
import type {
  ConflictDetailResponse,
  ConflictResponse,
  CostSnapshot,
  EvidenceListParams,
  EvidenceResponse,
  PageEnvelope,
  StageResponse,
  SubQuestionResponse,
  VerdictRequest,
  VerdictResponse
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

// GET /runs/{id}/evidence/{evidenceId}：单条证据详情（含全文 content）
// 卡片展开时懒加载（[前端详细设计 §9.4]：事件与列表只带 snippet，避免大 payload）。
// 注意：LLD §4.2.5 当前仅冻结证据池分页端点，本路径为 mock 先行形态，
// 后端 M2-4 契约冻结后若路径/参数有变，仅改本函数与 mock 路由。
export function getEvidence(runId: string, evidenceId: string): Promise<EvidenceResponse> {
  return http<EvidenceResponse>(
    `/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`
  )
}

// GET /runs/{id}/conflicts：run 下分歧列表
export function getRunConflicts(runId: string): Promise<ConflictResponse[]> {
  return http<ConflictResponse[]>(`/runs/${encodeURIComponent(runId)}/conflicts`)
}

// GET /conflicts/{id}：分歧详情（内嵌双方证据八项摘要，M2-2 冻结）
export function getConflict(conflictId: string): Promise<ConflictDetailResponse> {
  return http<ConflictDetailResponse>(`/conflicts/${encodeURIComponent(conflictId)}`)
}

// POST /conflicts/{id}/verdict：提交分歧裁决（M2-2）
// 409 code=conflict 表示该分歧已 resolved/abandoned；422 为 choice 非法或 reason 纯空白。
// M2 仅落地 API 层（可先行联调），裁决面板 UI 在 M3；末条 awaiting_human 裁决后后端自动续跑，无「继续」接口。
export function submitConflictVerdict(
  conflictId: string,
  body: VerdictRequest
): Promise<VerdictResponse> {
  return http<VerdictResponse>(`/conflicts/${encodeURIComponent(conflictId)}/verdict`, {
    method: 'POST',
    body: JSON.stringify(body)
  })
}

// GET /runs/{id}/cost/snapshot：成本快照（WS 断连补齐用）
export function getCostSnapshot(runId: string): Promise<CostSnapshot> {
  return http<CostSnapshot>(`/runs/${encodeURIComponent(runId)}/cost/snapshot`)
}
