// Mock 数据种子（[前端M0收尾方案 §4.3]）
// 初始化 fixtures 所需的基础数据

import { store, generateId, now } from './store'

export function seedInitialData(): void {
  // 当前用户
  store.users.set('mock-user-001', {
    id: 'mock-user-001',
    username: 'demo',
    email: 'demo@example.com',
    display_name: '演示用户',
    created_at: now()
  })

  // 示例项目
  const project1Id = generateId('proj')
  store.projects.set(project1Id, {
    id: project1Id,
    name: 'AI 技术发展调研',
    description: '调研 2024-2025 年 AI 领域的主要技术趋势',
    owner_id: 'mock-user-001',
    created_at: now(),
    updated_at: now()
  })

  // 示例任务
  const task1Id = generateId('task')
  store.tasks.set(task1Id, {
    id: task1Id,
    project_id: project1Id,
    title: '大语言模型发展趋势',
    description: '分析 GPT、Claude、Gemini 等主流大模型的技术演进',
    status: 'pending',
    created_at: now()
  })

  // 模板
  const template1Id = generateId('tmpl')
  store.templates.set(template1Id, {
    id: template1Id,
    name: '技术调研报告',
    description: '标准的技术调研报告模板',
    query_template: '请调研 {topic} 的技术发展现状、主要趋势和应用场景',
    created_at: now()
  })

  const template2Id = generateId('tmpl')
  store.templates.set(template2Id, {
    id: template2Id,
    name: '竞品分析',
    description: '产品竞品分析模板',
    query_template: '请分析 {product} 的主要竞品，对比功能、优劣势和市场定位',
    created_at: now()
  })
}
