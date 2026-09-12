// 意图路由 API（M2-1：POST /intent/classify，backend/app/api/v1/intent.py）
import { http } from '../http/http'
import type { IntentClassifyRequest, IntentClassifyResponse } from './types'

// 判别用户输入意图：chat 闲聊 / research 研究 / uncertain 不确定
// force 提供手动强制路径；LLM 不可用时后端保守降级 research 并置 degraded=true
export function classifyIntent(
  body: IntentClassifyRequest,
  options?: { idempotencyKey?: string }
): Promise<IntentClassifyResponse> {
  return http<IntentClassifyResponse>('/intent/classify', {
    method: 'POST',
    body: JSON.stringify(body),
    idempotencyKey: options?.idempotencyKey
  })
}
