// Mock 网关内存态（对齐 docs/contract/openapi-m1.json 冻结契约）
// 仅 dev 模式使用，关闭 dev server 即丢弃；时间字段统一为 ISO 8601 字符串

// 档位 token 预算（与后端 core.config 默认值一致）
export const TIER_TOKEN_BUDGET: Record<'quick' | 'standard' | 'deep' | 'extreme', number> = {
  quick: 50_000,
  standard: 150_000,
  deep: 400_000,
  extreme: 1_000_000
}

// mock 团队 ID：mock 不签发真实 JWT，令牌仅做等价的发放/轮换/校验
export const MOCK_TEAM_ID = '01team-mock0000000000000001'

// 用户视图（对齐 CurrentUser）
export interface MockUser {
  id: string
  team_id: string
  email: string
  display_name: string
  role: 'owner' | 'admin' | 'researcher' | 'reviewer'
  last_login_at?: string | null
}

// 项目视图（对齐 ProjectResponse）
export interface MockProject {
  id: string
  team_id: string
  owner_id: string
  name: string
  description?: string | null
  default_template_id?: string | null
  default_tier: 'quick' | 'standard' | 'deep' | 'extreme'
  status: 'active' | 'archived'
  created_at: string
  updated_at: string
}

// 研究运行视图（对齐 RunResponse）
export interface MockRun {
  id: string
  project_id: string
  creator_id: string
  template_id: string
  tier: 'quick' | 'standard' | 'deep' | 'extreme'
  question: string
  status: 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  current_stage?: string | null
  token_used: number
  token_budget: number
  started_at?: string | null
  finished_at?: string | null
  error_code?: string | null
  error_message?: string | null
  stream_url?: string | null
  created_at: string
  updated_at: string
}

// 报告视图（对齐 ReportResponse）
export interface MockReport {
  id: string
  run_id: string
  template_id: string
  status: 'draft' | 'final' | 'superseded'
  content_md: string
  token_used: number
  created_at: string
  updated_at: string
}

// 全局内存态
export const store = {
  users: new Map<string, MockUser>(),
  projects: new Map<string, MockProject>(),
  runs: new Map<string, MockRun>(),
  reportsByRunId: new Map<string, MockReport>(),
  // 已签发的令牌池：登录后方有令牌；access 只增；refresh 在刷新时轮换（旧 token 删除）
  accessTokens: new Set<string>(),
  refreshTokens: new Set<string>(),
  // 令牌序号：保证每次签发的轮换令牌唯一
  tokenSeq: 0,
  currentUserId: '01user-mock00000000000001'
}

// 生成 ULID 风格的 26 位小写 ID（与后端 ID 长度形态一致）
export function generateId(): string {
  const time = Date.now().toString(36).padStart(10, '0')
  const rand = Array.from({ length: 16 }, () =>
    Math.floor(Math.random() * 36).toString(36)
  ).join('')
  return `${time}${rand}`
}

// 当前 UTC 时间的 ISO 字符串
export function nowIso(): string {
  return new Date().toISOString()
}

// 签发一对新令牌（登录 / 刷新共用）
export function issueTokenPair(): { accessToken: string; refreshToken: string } {
  store.tokenSeq += 1
  const accessToken = `mock-access-token-${store.tokenSeq}`
  const refreshToken = `mock-refresh-token-${store.tokenSeq}`
  store.accessTokens.add(accessToken)
  store.refreshTokens.add(refreshToken)
  return { accessToken, refreshToken }
}
