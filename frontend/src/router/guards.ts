import type { Router, RouteLocationNormalized } from 'vue-router'
import { useSessionStore } from '@/stores/session'

// 守卫装配：实现 [前端详细设计 §4.3 / §14.1]
//  - 每次导航先 await 会话静默恢复（内部单飞，只实际执行一次）
//  - requiresAuth 拦截未登录访问并保留 redirect
//  - 已登录访问登录页直接进入项目列表
//  - 项目边界校验留给 store/页面级资源接口（统一按 404 处理，越权不区分 403）
export function installGuards(router: Router): void {
  router.beforeEach(async (to: RouteLocationNormalized) => {
    if (to.meta?.title) {
      document.title = `${String(to.meta.title)} · AI 研究者助手`
    }

    const session = useSessionStore()
    await session.restore()

    if (to.meta?.public) {
      if (to.name === 'auth-login' && session.isAuthenticated) {
        return { path: '/projects' }
      }
      return true
    }

    if (!session.isAuthenticated) {
      return {
        name: 'auth-login',
        query: { redirect: to.fullPath }
      }
    }
    return true
  })
}
