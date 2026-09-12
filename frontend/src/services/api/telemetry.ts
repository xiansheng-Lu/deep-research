// 埋点上报 API（M2 起：POST /telemetry/batch，[前端详细设计 §16]）
import { http } from '../http/http'
import type { TelemetryBatchRequest } from './types'

// 批量上报埋点事件；该接口为辅助通道，调用方应吞掉失败（不影响业务页面）
export function postTelemetry(body: TelemetryBatchRequest): Promise<void> {
  return http<void>('/telemetry/batch', {
    method: 'POST',
    body: JSON.stringify(body)
  })
}
