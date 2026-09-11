// 鉴权 API（openapi-m1：/auth/login、/auth/refresh、/auth/logout、/auth/me）
import { http } from '../http/http'
import type { CurrentUser, LoginRequest, TokenPair } from './types'

// 登录：返回令牌对；skipAuth 避免注入旧 token 与 401 刷新重放
export function login(body: LoginRequest): Promise<TokenPair> {
  return http<TokenPair>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(body),
    skipAuth: true
  })
}

// 刷新令牌：用 refresh_token 换新令牌对
export function refreshTokenPair(refreshToken: string): Promise<TokenPair> {
  return http<TokenPair>('/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
    skipAuth: true
  })
}

// 当前登录用户
export function getCurrentUser(): Promise<CurrentUser> {
  return http<CurrentUser>('/auth/me')
}

// 登出（后端仅记录登出时间，令牌失效由过期时间保证）
export function logout(): Promise<void> {
  return http<void>('/auth/logout', { method: 'POST' })
}
