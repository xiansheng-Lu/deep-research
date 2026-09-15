// 埋点上报 API（M2 起：POST /telemetry/batch，[前端详细设计 §16]）
import { http } from '../http/http'
import type { TelemetryBatchRequest } from './types'

// 批量上报埋点事件；该接口为辅助通道，调用方应吞掉失败（不影响业务页面）。
// keepalive 用于页面隐藏/卸载时的补发，浏览器允许请求比页面活得更久。
export function postTelemetry(body: TelemetryBatchRequest, keepalive = false): Promise<void> {
  return http<void>('/telemetry/batch', {
    method: 'POST',
    body: JSON.stringify(body),
    keepalive
  })
}
