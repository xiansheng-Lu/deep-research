import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  login as apiLogin,
  logout as apiLogout,
  getCurrentUser,
  refreshTokenPair
} from '@/services/api/auth'
import { setAuthProvider } from '@/services/http/http'
import { isAuthError, type ApiError } from '@/services/http/error'
import type { CurrentUser, TokenPair } from '@/services/api/types'
import { useProjectStore } from '@/stores/project'

// 会话/登录态（见 [前端详细设计 §14.1 / openapi-m1 §鉴权]）
// access token 仅内存持有，refresh token 落 localStorage；
// 刷新以单飞 Promise 防守卫与并发请求重复触发。
const REFRESH_KEY = 'refresh_token'

// restore 单飞：模块级共享，路由守卫每次导航 await 同一次恢复
let restorePromise: Promise<boolean> | null = null
// AuthProvider 只需注册一次（闭包引用当前 store 的响应式状态）
let authProviderInstalled = false

export const useSessionStore = defineStore('session', () => {
  const accessToken = ref<string | null>(null)
  const refreshToken = ref<string | null>(loadRefresh())
  const user = ref<CurrentUser | null>(null)
  // 最近一次静默恢复遇到的连接类故障（网络错误/5xx）：
  // 非空表示“凭据状态未知但用户不应被登出”，守卫放行、页面展示无法连接错误态
  const restoreError = ref<ApiError | null>(null)

  const isAuthenticated = computed(() => Boolean(accessToken.value))

  function applyTokens(pair: TokenPair): void {
    accessToken.value = pair.access_token
    refreshToken.value = pair.refresh_token
    localStorage.setItem(REFRESH_KEY, pair.refresh_token)
  }

  function clear(): void {
    accessToken.value = null
    refreshToken.value = null
    user.value = null
    restoreError.value = null
    localStorage.removeItem(REFRESH_KEY)
  }

  // 登录：换取令牌对后立即拉取当前用户，任一步失败由调用方展示错误
  async function login(email: string, password: string): Promise<void> {
    const pair = await apiLogin({ email, password })
    applyTokens(pair)
    restoreError.value = null
    user.value = await getCurrentUser()
  }

  // 登出：后端为软登出（仅记录登出时间，JWT 到期前仍有效）；
  // 该接口仅为通知性质，网络失败不阻断本地会话清理
  async function logout(): Promise<void> {
    try {
      await apiLogout()
    } catch {
      // 忽略通知接口失败，保证本地会话一定被清理
    }
    useProjectStore().clear()
    clear()
  }

  async function fetchMe(): Promise<void> {
    user.value = await getCurrentUser()
  }

  // http 层 401 刷新流程调用：用 refresh token 换新令牌对。
  // 401/403（refresh token 失效）返回 null，由 http 层触发 onAuthExpired 跳登录；
  // 网络错误/5xx 向上抛出，调用请求随失败进入错误态，绝不静默登出
  async function refreshWithStoredToken(): Promise<string | null> {
    const current = refreshToken.value
    if (!current) return null
    try {
      const pair = await refreshTokenPair(current)
      applyTokens(pair)
      restoreError.value = null
      return pair.access_token
    } catch (err) {
      if (isAuthError(err)) return null
      throw err
    }
  }

  // 静默恢复：有 refresh token 则换新对并拉用户。
  // 仅 401/403（凭据明确失效）清会话；网络错误/5xx 保留 refresh token 并记录
  // restoreError，由守卫放行到目标页错误态，后端恢复后经页面重试自动拉回会话
  async function doRestore(): Promise<boolean> {
    if (accessToken.value) return true
    if (!refreshToken.value) return false
    try {
      const pair = await refreshTokenPair(refreshToken.value)
      applyTokens(pair)
      user.value = await getCurrentUser()
      restoreError.value = null
      return true
    } catch (err) {
      if (isAuthError(err)) {
        clear()
        return false
      }
      // 连接类故障（网络/5xx）保留会话；其余非鉴权异常同样不主动登出，
      // 交由具体页面错误态呈现，避免一次偶发故障把用户踢回登录页
      restoreError.value = err as ApiError
      return false
    }
  }

  // 守卫入口：恢复只实际执行一次，并发导航共享同一 Promise
  function restore(): Promise<boolean> {
    if (accessToken.value) return Promise.resolve(true)
    if (!restorePromise) {
      restorePromise = doRestore().finally(() => {
        restorePromise = null
      })
    }
    return restorePromise
  }

  // 刷新彻底失败（refresh token 也失效）：整页跳到登录页并保留回跳地址
  function handleAuthExpired(): void {
    clear()
    if (window.location.pathname.startsWith('/auth/')) return
    const redirect = window.location.pathname + window.location.search
    window.location.assign(`/auth/login?redirect=${encodeURIComponent(redirect)}`)
  }

  if (!authProviderInstalled) {
    authProviderInstalled = true
    setAuthProvider({
      getAccessToken: () => accessToken.value,
      refresh: refreshWithStoredToken,
      onAuthExpired: handleAuthExpired
    })
  }

  return {
    accessToken,
    refreshToken,
    user,
    restoreError,
    isAuthenticated,
    login,
    logout,
    fetchMe,
    restore,
    clear
  }
})

function loadRefresh(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}
