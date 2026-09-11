import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { installGuards } from './guards'

// 路由表：依据 [前端详细设计 §4.2]
//  - 公共路由（登录/错误）直接渲染
//  - 受保护路由以 AppShell 为父布局，嵌套业务视图
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: () => ({ path: '/projects' })
  },
  {
    path: '/auth/login',
    name: 'auth-login',
    component: () => import('@/views/auth/LoginView.vue'),
    meta: { public: true, title: '登录' }
  },
  {
    path: '/',
    component: () => import('@/layouts/AppShell.vue'),
    children: [
      {
        path: 'home',
        name: 'home',
        component: () => import('@/views/home/HomeView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '首页' }
      },
      {
        path: 'assistant',
        name: 'assistant',
        component: () => import('@/views/assistant/AssistantView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '助手' }
      },
      {
        path: 'projects',
        name: 'projects',
        component: () => import('@/views/project/ProjectListView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '项目' }
      },
      {
        path: 'projects/:projectId/tasks',
        name: 'project-tasks',
        component: () => import('@/views/project/ProjectTasksView.vue'),
        meta: { requiresAuth: true, nav: 'project', title: '任务' }
      },
      {
        path: 'projects/:projectId/runs/:runId/cockpit',
        name: 'cockpit',
        component: () => import('@/views/cockpit/CockpitView.vue'),
        meta: { requiresAuth: true, nav: 'project', title: '指挥舱' }
      },
      {
        path: 'projects/:projectId/runs/:runId/report',
        name: 'report',
        component: () => import('@/views/report/ReportView.vue'),
        meta: { requiresAuth: true, nav: 'project', title: '报告' }
      },
      {
        path: 'projects/:projectId/runs/:runId/disputes',
        name: 'disputes',
        component: () => import('@/views/disputes/DisputesView.vue'),
        meta: { requiresAuth: true, nav: 'project', title: '分歧工作台' }
      },
      {
        path: 'wizard',
        name: 'wizard',
        component: () => import('@/views/wizard/WizardView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '发起研究' }
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('@/views/templates/TemplateLibraryView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '模板库' }
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/knowledge/KnowledgeView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '知识库' }
      },
      {
        path: 'account',
        name: 'account',
        component: () => import('@/views/account/AccountView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '账户' }
      },
      {
        path: 'account/team',
        name: 'account-team',
        component: () => import('@/views/account/AccountTeamView.vue'),
        meta: { requiresAuth: true, nav: 'global', title: '团队' }
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/errors/NotFoundView.vue'),
    meta: { public: true, title: '未找到' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  }
})

installGuards(router)

export default router
