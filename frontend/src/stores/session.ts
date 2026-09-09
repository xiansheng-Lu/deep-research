import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// 会话/登录态：access 仅内存持有，refresh 落 localStorage（见 [前端详细设计 §14.1]）
export interface SessionUser {
  id: string
  name: string
  email: string
  role: 'owner' | 'admin' | 'researcher' | 'reviewer'
}

const REFRESH_KEY = 'refresh_token'

export const useSessionStore = defineStore('session', () => {
  const accessToken = ref<string | null>(null)
  const refreshToken = ref<string | null>(loadRefresh())
  const user = ref<SessionUser | null>(null)

  const isAuthenticated = computed(() => Boolean(accessToken.value))

  function setAccessToken(token: string | null) {
    accessToken.value = token
  }

  function setRefreshToken(token: string | null) {
    refreshToken.value = token
    if (token) {
      localStorage.setItem(REFRESH_KEY, token)
    } else {
      localStorage.removeItem(REFRESH_KEY)
    }
  }

  function setUser(next: SessionUser | null) {
    user.value = next
  }

  function clear() {
    accessToken.value = null
    refreshToken.value = null
    user.value = null
    localStorage.removeItem(REFRESH_KEY)
  }

  return {
    accessToken,
    refreshToken,
    user,
    isAuthenticated,
    setAccessToken,
    setRefreshToken,
    setUser,
    clear
  }
})

function loadRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY)
  } catch {
    return null
  }
}
