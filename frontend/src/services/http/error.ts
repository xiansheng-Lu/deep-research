// 统一 ApiError 形态（[前端详细设计 §8.2 § 错误归一]，WP-17 联调韧性）
// kind 区分故障来源：
// - http：服务器有响应（含 4xx/5xx），页面按 status 分流（401 刷新、404 空态、5xx 重试）
// - network：请求未到达服务器或无响应（断网、DNS、连接被拒、网关无上游），
//   语义为“稍后可能恢复”，会话不清理、错误态自动重试
export type ApiErrorKind = 'http' | 'network'

export interface ApiError {
  // 网络层错误固定为 0；HTTP 错误为响应状态码
  status: number
  code: string
  title: string
  detail?: string
  traceId?: string
  kind: ApiErrorKind
}

// 会话失效：仅 401/403 属于凭据问题，需要清会话跳登录
export function isAuthError(err: unknown): boolean {
  return (
    typeof err === 'object' &&
    err !== null &&
    'status' in err &&
    ((err as ApiError).status === 401 || (err as ApiError).status === 403)
  )
}

// 可恢复的连接类故障：无响应（断网）或服务端 5xx；语义为“稍后可能恢复”，
// 不清理会话，错误态自动重试
export function isRecoverableServerError(err: unknown): boolean {
  if (typeof err !== 'object' || err === null || !('status' in err)) return false
  const status = (err as ApiError).status
  return status === 0 || status >= 500
}
