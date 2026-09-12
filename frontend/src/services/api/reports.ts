// 报告 API（M1：GET /runs/{run_id}/report、GET /reports/{report_id}；
// M2-6：GET /reports/{report_id}/citations 数据点溯源索引）
import { http } from '../http/http'
import type { ReportCitationItem, ReportResponse } from './types'

// 按运行获取报告（指挥舱终态后进入报告页使用）
export function getRunReport(runId: string): Promise<ReportResponse> {
  return http<ReportResponse>(`/runs/${encodeURIComponent(runId)}/report`)
}

// 按运行获取报告（契约 GET /reports/{run_id}：M1 路径参数语义同 run_id；
// 报告未生成时后端返回 422 validation_error。M4 起该参数才作为独立 report id 使用）
export function getReport(runId: string): Promise<ReportResponse> {
  return http<ReportResponse>(`/reports/${encodeURIComponent(runId)}`)
}

// 获取报告引用列表（数据点级溯源：角标 ↔ 证据 URL/原文片段，M2-6 冻结）
export function getReportCitations(reportId: string): Promise<ReportCitationItem[]> {
  return http<ReportCitationItem[]>(
    `/reports/${encodeURIComponent(reportId)}/citations`
  )
}
