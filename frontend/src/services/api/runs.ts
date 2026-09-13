// 研究运行 API（openapi-m1：POST /runs、GET /runs/{run_id}；M2-4：GET /runs 列表）
import { http } from '../http/http'
import type { CreateRunRequest, PageEnvelope, RunListParams, RunResponse } from './types'

// 创建研究运行；project_id 在请求体中（契约冻结口径）
// options.idempotencyKey 透传 Idempotency-Key 头，重试必须复用同一键
export function createRun(
  body: CreateRunRequest,
  options?: { idempotencyKey?: string }
): Promise<RunResponse> {
  return http<RunResponse>('/runs', {
    method: 'POST',
    body: JSON.stringify(body),
    idempotencyKey: options?.idempotencyKey
  })
}

// 查询运行详情（含 status / current_stage / token 用量）
export function getRun(runId: string): Promise<RunResponse> {
  return http<RunResponse>(`/runs/${encodeURIComponent(runId)}`)
}

// 创建者维度运行列表（M2-4：关闭首页“进行中研究”本地索引，改走后端数据）。
// 注意：这是创建者维度；项目维度 GET /projects/{id}/runs 属 M3，勿提前接。
export function listMyRuns(params?: RunListParams): Promise<PageEnvelope<RunResponse>> {
  const search = new URLSearchParams()
  if (params?.status) search.set('status', params.status)
  if (params?.project_id) search.set('project_id', params.project_id)
  if (params?.page !== undefined) search.set('page', String(params.page))
  if (params?.page_size !== undefined) search.set('page_size', String(params.page_size))
  const query = search.toString()
  return http<PageEnvelope<RunResponse>>(`/runs${query ? `?${query}` : ''}`)
}

// 派生运行实时流 WS 地址：后端统一 query 参数鉴权（/api/v1/ws/runs/{id}/stream?token=）
// 同源页面下将 http(s) 基址替换为 ws(s)；dev 环境走 /api 前缀，Vite 代理已开启 ws 转发
export function buildRunStreamUrl(runId: string, token: string): string {
  const path = `/api/v1/ws/runs/${encodeURIComponent(runId)}/stream?token=${encodeURIComponent(token)}`
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}${path}`
}
