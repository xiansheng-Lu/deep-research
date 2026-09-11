// M1 契约类型（手写对齐 docs/contract/openapi-m1.json）
// 字段名与后端 FastAPI 响应保持 snake_case 原样，不做驼峰转换；
// M2+ 端点规模扩大后评估切换 openapi-generator typescript-fetch 生成。

// ─── 领域枚举 ───

// 研究档位（CreateRunRequest.tier / ProjectResponse.default_tier）
export type RunTier = 'quick' | 'standard' | 'deep' | 'extreme'

// run 生命周期状态（RunResponse.status）
export type RunStatus = 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'

// 研究六阶段（后端 orchestrator state.py ResearchStage）
// 顺序即流水线推进顺序，指挥舱时间线以此为准
export type ResearchStageName =
  | 'clarify'
  | 'decompose'
  | 'retrieve'
  | 'standardize'
  | 'critique'
  | 'report'

// 报告状态（ReportResponse.status）
export type ReportStatus = 'draft' | 'final' | 'superseded'

// 项目状态（ProjectResponse.status）
export type ProjectStatus = 'active' | 'archived'

// ─── 鉴权 ───

// POST /auth/login 请求体
export interface LoginRequest {
  email: string
  password: string
}

// POST /auth/refresh 请求体（纯 JSON，不依赖 Cookie）
export interface RefreshRequest {
  refresh_token: string
}

// login / refresh 响应：访问令牌 + 刷新令牌对
export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type?: string
  expires_in: number
}

// GET /auth/me 响应：当前登录用户视图
export interface CurrentUser {
  id: string
  team_id: string
  email: string
  display_name: string
  role: string
  last_login_at?: string | null
}

// ─── 项目 ───

// GET /projects 响应元素
export interface ProjectResponse {
  id: string
  team_id: string
  owner_id: string
  name: string
  description?: string | null
  default_template_id?: string | null
  default_tier: RunTier
  status: ProjectStatus
  created_at: string
  updated_at: string
}

// POST /projects 请求体
export interface CreateProjectRequest {
  name: string
  description?: string | null
  default_tier?: RunTier
  default_template_id?: string | null
}

// ─── 研究运行 ───

// POST /runs 请求体
export interface CreateRunRequest {
  project_id: string
  question: string
  tier?: RunTier
  template_id?: string | null
}

// GET /runs/{run_id} 响应
export interface RunResponse {
  id: string
  project_id: string
  creator_id: string
  template_id: string
  tier: RunTier
  question: string
  status: RunStatus
  current_stage?: ResearchStageName | null
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

// ─── 报告 ───

// GET /runs/{run_id}/report 与 GET /reports/{report_id} 响应：含 Markdown 正文
export interface ReportResponse {
  id: string
  run_id: string
  template_id: string
  status: ReportStatus
  content_md: string
  token_used: number
  created_at: string
  updated_at: string
}
