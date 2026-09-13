// HttpClient（[前端详细设计 §8.2]）
// 职责：相对路径拼接 API 基址、Authorization 注入、后端错误体归一、
//       401 时 single-flight 刷新 access token 并重放原请求。
// 鉴权状态不直接依赖 pinia store（避免循环依赖），由应用启动时注册 AuthProvider。
import type { ApiError } from './error'
import { zhCN } from '@/services/i18n/zh-CN'

// API 基址：开发环境由 Vite 代理 / mock 网关插件拦截 /api 前缀，
// 生产环境前后端同源部署，由反向网关转发，故统一使用同源相对路径。
const API_BASE = '/api/v1'

export interface HttpOptions extends RequestInit {
  /** 用于触发同一请求的重试复用 */
  idempotencyKey?: string
  /** 跳过鉴权头注入与 401 刷新重放（login / refresh 请求本身使用） */
  skipAuth?: boolean
}

// 鉴权提供者：由 session store 在应用启动时注册
export interface AuthProvider {
  /** 当前内存中的 access token */
  getAccessToken: () => string | null
  /** 用 refresh token 换新 access token；成功返回新 token，失败返回 null */
  refresh: () => Promise<string | null>
  /** 刷新彻底失败（refresh token 也过期）时的登出回调 */
  onAuthExpired?: () => void
}

let authProvider: AuthProvider | null = null

export function setAuthProvider(provider: AuthProvider | null): void {
  authProvider = provider
}

// single-flight 刷新：多个并发请求同时遇 401 时共享同一次刷新
let refreshing: Promise<string | null> | null = null

function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = Promise.resolve(authProvider?.refresh() ?? null).finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

export async function http<T>(input: string, init: HttpOptions = {}): Promise<T> {
  const response = await request(input, init, true)
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

// 流式（SSE）专用：同样完成鉴权注入与 401 刷新重放，但返回原始 Response，
// 由调用方按 text/event-stream 自行读取 body（如 POST /assistant/chat）。
export function httpStream(input: string, init: HttpOptions = {}): Promise<Response> {
  return request(input, init, true)
}

// allowRetry=false 表示该请求已经是刷新后的重放，再次 401 不再重试
async function request(
  input: string,
  init: HttpOptions,
  allowRetry: boolean
): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.idempotencyKey) {
    headers.set('Idempotency-Key', init.idempotencyKey)
  }
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const token = authProvider?.getAccessToken()
  if (!init.skipAuth && token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  let response: Response
  try {
    response = await fetch(`${API_BASE}${input}`, { ...init, headers })
  } catch (err) {
    // WP-17：请求未获得任何响应（断网、DNS 失败、连接被拒、CORS 等）。
    // 调用方主动取消（AbortController）不视为网络故障，原样抛出由调用方处理。
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw toNetworkError()
  }

  if (!response.ok) {
    // 401：非鉴权接口自身的失败时，尝试刷新一次并重放原请求
    if (response.status === 401 && allowRetry && !init.skipAuth && authProvider) {
      const newToken = await refreshAccessToken()
      if (newToken) {
        return request(input, init, false)
      }
      authProvider.onAuthExpired?.()
    }
    throw await toApiError(response)
  }
  return response
}

// 网络层故障（无 HTTP 响应）：status 固定 0，会话层与错误态据此走“保留会话+自动重试”
function toNetworkError(): ApiError {
  return {
    status: 0,
    code: 'network_error',
    title: zhCN.errors.network,
    kind: 'network'
  }
}

// 无业务错误体时按状态码给中文兜底标题，避免直接向用户暴露英文 statusText
function fallbackTitle(status: number): string {
  if (status === 401) return zhCN.errors.unauthorized
  if (status === 404) return zhCN.errors.notFound
  if (status === 502 || status === 503 || status === 504) return zhCN.errors.badGateway
  if (status >= 500) return zhCN.errors.server
  return zhCN.errors.unknown
}

async function toApiError(response: Response): Promise<ApiError> {
  // 后端错误体：业务异常 {code, message, details}（main.py AppError handler）；
  // FastAPI 校验错误 {detail: [...]}；按字段存在性归一为 ApiError
  let body: Record<string, unknown> = {}
  try {
    body = (await response.json()) as Record<string, unknown>
  } catch {
    // 响应体非 JSON（网关 HTML 错误页等）时按状态码走中文兜底
  }
  const detail =
    typeof body.detail === 'string'
      ? body.detail
      : typeof body.message === 'string'
        ? body.message
        : Array.isArray(body.detail)
          ? JSON.stringify(body.detail)
          : response.status >= 500
            ? zhCN.errors.serverDetail
            : ''
  return {
    status: response.status,
    code: typeof body.code === 'string' ? body.code : `HTTP_${response.status}`,
    title: typeof body.title === 'string' ? body.title : fallbackTitle(response.status),
    detail,
    traceId:
      typeof body.trace_id === 'string' ? body.trace_id : response.headers.get('x-trace-id') ?? undefined,
    kind: 'http'
  }
}
