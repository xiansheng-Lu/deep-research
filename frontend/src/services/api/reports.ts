// 报告 API（openapi-m1：GET /runs/{run_id}/report、GET /reports/{report_id}）
import { http } from '../http/http'
import type { ReportResponse } from './types'

// 按运行获取报告（指挥舱终态后进入报告页使用）
export function getRunReport(runId: string): Promise<ReportResponse> {
  return http<ReportResponse>(`/runs/${encodeURIComponent(runId)}/report`)
}

// 按报告 ID 获取（M4 分享只读链接的底座，M1 预留）
export function getReport(reportId: string): Promise<ReportResponse> {
  return http<ReportResponse>(`/reports/${encodeURIComponent(reportId)}`)
}
