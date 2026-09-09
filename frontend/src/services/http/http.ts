// HttpClient 骨架（[前端详细设计 §8.2]）
// M0 仅提供 fetch 包装与错误归一；鉴权注入、single-flight refresh、幂等键在 M1 起接入
import type { ApiError } from './error'

export interface HttpOptions extends RequestInit {
  /** 用于触发同一请求的重试复用 */
  idempotencyKey?: string
}

export async function http<T>(input: string, init: HttpOptions = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.idempotencyKey) {
    headers.set('Idempotency-Key', init.idempotencyKey)
  }
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }

  const response = await fetch(input, { ...init, headers })

  if (!response.ok) {
    throw await toApiError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

async function toApiError(response: Response): Promise<ApiError> {
  // RFC 7807 application/problem+json；前端按 code 分支（§8.2）
  let body: Record<string, unknown> = {}
  try {
    body = (await response.json()) as Record<string, unknown>
  } catch {
    // 响应体非 JSON 时保留 status 用于回退
  }
  return {
    status: response.status,
    code: typeof body.code === 'string' ? body.code : `HTTP_${response.status}`,
    title: typeof body.title === 'string' ? body.title : response.statusText,
    detail: typeof body.detail === 'string' ? body.detail : '',
    traceId: typeof body.trace_id === 'string' ? body.trace_id : response.headers.get('x-trace-id') ?? undefined
  }
}
