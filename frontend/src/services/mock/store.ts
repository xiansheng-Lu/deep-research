// Mock 网关内存态（[前端M0收尾方案 §4.3]）
// 仅 dev 模式使用，关闭 dev server 即丢弃

export interface MockUser {
  id: string
  username: string
  email: string
  display_name: string
  created_at: number
}

export interface MockProject {
  id: string
  name: string
  description: string
  owner_id: string
  created_at: number
  updated_at: number
}

export interface MockTask {
  id: string
  project_id: string
  title: string
  description: string
  status: 'pending' | 'in_progress' | 'completed'
  created_at: number
}

export interface MockRun {
  id: string
  project_id: string
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'
  query: string
  template_id?: string
  created_at: number
  updated_at: number
  started_at?: number
  completed_at?: number
  cost_used: number
  token_used: number
  stages: Array<{
    name: string
    status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
    started_at?: number
    completed_at?: number
    attempt?: number
  }>
  report_id?: string
  fixture?: 'happy_path' | 'demo_full'
  last_cost_warning?: {
    level: '70' | '90'
    used: number
    budget: number
    ratio: number
    at: number
  }
  pending_interrupt?: {
    type: 'clarification'
    reason?: string
    questions: Array<{
      id: string
      text: string
      type: 'single' | 'multi' | 'text'
      options?: Array<{ key: string; label: string; recommended?: boolean }>
    }>
    expires_in_seconds?: number
    at: number
  }
}

export interface MockConflict {
  id: string
  run_id: string
  claim_a: string
  claim_b: string
  severity: 'low' | 'medium' | 'high'
  status: 'pending' | 'resolved'
  verdict?: 'a' | 'b' | 'merge'
  created_at: number
}

export interface MockReport {
  id: string
  run_id: string
  title: string
  blocks: Array<{
    id: string
    type: 'heading' | 'paragraph' | 'evidence' | 'conclusion'
    content: string
    order: number
  }>
  created_at: number
}

export interface MockTemplate {
  id: string
  name: string
  description: string
  query_template: string
  created_at: number
}

// 全局内存态
export const store = {
  users: new Map<string, MockUser>(),
  projects: new Map<string, MockProject>(),
  tasks: new Map<string, MockTask>(),
  runs: new Map<string, MockRun>(),
  conflicts: new Map<string, MockConflict>(),
  reports: new Map<string, MockReport>(),
  templates: new Map<string, MockTemplate>(),
  currentUserId: 'mock-user-001'
}

// 辅助函数
export function generateId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function now(): number {
  return Date.now()
}
