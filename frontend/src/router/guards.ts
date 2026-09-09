import type { Router, RouteLocationNormalized } from 'vue-router'
import { useSessionStore } from '@/stores/session'

// 守卫装配：实现 [前端详细设计 §4.3]
//  - requiresAuth 拦截未登录访问并保留 redirect
//  - 项目边界校验留给 store/页面级资源接口（统一按 404 处理，越权不区分 403）
export function installGuards(router: Router): void {
  router.beforeEach((to: RouteLocationNormalized) => {
    if (to.meta?.title) {
      document.title = `${String(to.meta.title)} · AI 研究者助手`
    }
    if (to.meta?.public) return true

    const session = useSessionStore()
    if (!session.accessToken) {
      return {
        name: 'auth-login',
        query: { redirect: to.fullPath }
      }
    }
    return true
  })
}
