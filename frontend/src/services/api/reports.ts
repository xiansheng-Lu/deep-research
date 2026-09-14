// 报告 API（M1：GET /runs/{run_id}/report、GET /reports/{report_id}；
// M2-7：GET /reports/{report_id}/citations 数据点溯源索引）
import { http } from '../http/http'
import type { ReportCitationItem, ReportResponse, StructuredReportResponse } from './types'

// 按运行获取报告（指挥舱终态后进入报告页使用）
export function getRunReport(runId: string): Promise<ReportResponse> {
  return http<ReportResponse>(`/runs/${encodeURIComponent(runId)}/report`)
}

// 按运行获取报告（契约 GET /reports/{run_id}：M1 路径参数语义同 run_id；
// 报告未生成时后端返回 422 validation_error。M4 起该参数才作为独立 report id 使用）
export function getReport(runId: string): Promise<ReportResponse> {
  return http<ReportResponse>(`/reports/${encodeURIComponent(runId)}`)
}

// 获取结构化终稿（WP-16 blocks 轨道）。
// M2-7 契约冻结前与 getReport 同路径：mock 对结构化剧本返回 markdown+blocks 超集；
// 真实后端当前仅回 markdown（无 blocks 字段，页面据此走 markdown 轨道）。
// M2-7 冻结后若路径或形态调整，只改本函数与 StructuredReportResponse 类型。
export function getStructuredReport(runId: string): Promise<StructuredReportResponse> {
  return http<StructuredReportResponse>(`/reports/${encodeURIComponent(runId)}`)
}

// 获取报告引用列表（数据点级溯源：角标 ↔ 证据 URL/原文片段，M2-7 冻结）
export function getReportCitations(reportId: string): Promise<ReportCitationItem[]> {
  return http<ReportCitationItem[]>(
    `/reports/${encodeURIComponent(reportId)}/citations`
  )
}
