import type { Router, RouteLocationNormalized } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { isRecoverableServerError } from '@/services/http/error'

// 守卫装配：实现 [前端详细设计 §4.3 / §14.1]
//  - 每次导航先 await 会话静默恢复（内部单飞，只实际执行一次）
//  - requiresAuth 拦截未登录访问并保留 redirect
//  - 已登录访问登录页直接进入项目列表
//  - 静默恢复遇网络错误/5xx（WP-17）：凭据状态未知但 refresh token 仍在，
//    放行进入目标页展示“无法连接服务器”错误态并自动重试，绝不踢回登录页；
//    后端恢复后页面请求触发的 401 刷新会自动拉回会话
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
      if (session.restoreError && isRecoverableServerError(session.restoreError)) {
        return true
      }
      return {
        name: 'auth-login',
        query: { redirect: to.fullPath }
      }
    }
    return true
  })
}
