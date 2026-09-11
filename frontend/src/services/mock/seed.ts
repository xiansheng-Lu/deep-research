// Mock 数据种子：初始化联调所需的用户与示例项目（openapi-m1 实体范围）

import { store, generateId, nowIso, MOCK_TEAM_ID } from './store'

export function seedInitialData(): void {
  // 当前演示用户；登录邮箱 demo@example.com，mock 接受任意非空密码
  store.users.set(store.currentUserId, {
    id: store.currentUserId,
    team_id: MOCK_TEAM_ID,
    email: 'demo@example.com',
    display_name: '演示用户',
    role: 'owner',
    last_login_at: null
  })

  // 示例项目一
  const project1Id = generateId()
  store.projects.set(project1Id, {
    id: project1Id,
    team_id: MOCK_TEAM_ID,
    owner_id: store.currentUserId,
    name: 'AI 技术发展调研',
    description: '调研 2024-2026 年 AI 领域的主要技术趋势',
    default_template_id: null,
    default_tier: 'standard',
    status: 'active',
    created_at: nowIso(),
    updated_at: nowIso()
  })

  // 示例项目二
  const project2Id = generateId()
  store.projects.set(project2Id, {
    id: project2Id,
    team_id: MOCK_TEAM_ID,
    owner_id: store.currentUserId,
    name: '竞品追踪：AI 编程助手',
    description: '持续跟踪主流 AI 编程助手的能力演进与定价策略',
    default_template_id: null,
    default_tier: 'deep',
    status: 'active',
    created_at: nowIso(),
    updated_at: nowIso()
  })
}
