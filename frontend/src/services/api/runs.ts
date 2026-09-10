// 研究运行 API（openapi-m1：POST /runs、GET /runs/{run_id}）
import { http } from '../http/http'
import type { CreateRunRequest, RunResponse } from './types'

// 创建研究运行；project_id 在请求体中（契约冻结口径）
export function createRun(body: CreateRunRequest): Promise<RunResponse> {
  return http<RunResponse>('/runs', {
    method: 'POST',
    body: JSON.stringify(body)
  })
}

// 查询运行详情（含 status / current_stage / token 用量）
export function getRun(runId: string): Promise<RunResponse> {
  return http<RunResponse>(`/runs/${encodeURIComponent(runId)}`)
}

// 派生运行实时流 WS 地址：后端统一 query 参数鉴权（/api/v1/ws/runs/{id}/stream?token=）
// 同源页面下将 http(s) 基址替换为 ws(s)；dev 环境由 Vite 代理 /ws 前缀
export function buildRunStreamUrl(runId: string, token: string): string {
  const path = `/api/v1/ws/runs/${encodeURIComponent(runId)}/stream?token=${encodeURIComponent(token)}`
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}${path}`
}
