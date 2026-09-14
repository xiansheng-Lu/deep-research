// Mock 网关内存态（对齐 docs/contract/openapi-m1.json 冻结契约）
// 仅 dev 模式使用，关闭 dev server 即丢弃；时间字段统一为 ISO 8601 字符串
// 本文件随 mock 网关在 vite.config 的 Node 上下文加载，类型引用用相对路径，禁止 @/ 别名
import type { ConflictType } from '../../types/domain'

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

// ─── M2 看板实体（WP-10：事件归约落内存态，REST 列表与 WS 广播同源）───

// 阶段执行记录（对齐 StageResponse）
export interface MockStageRecord {
  id: string
  run_id: string
  name: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
  attempt: number
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

// 子问题（对齐 SubQuestionResponse）
export interface MockSubQuestion {
  id: string
  run_id: string
  question: string
  depends_on: string[]
  status: 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'evidence_short'
  evidence_count: number
  created_at: string
  updated_at: string
}

// 证据（对齐 EvidenceResponse；content 懒加载字段仅在详情形态填充）
export interface MockEvidence {
  id: string
  run_id: string
  sub_question_id: string
  url: string
  domain: string
  title: string
  snippet: string
  content?: string | null
  source_type: 'official_doc' | 'news' | 'community' | 'search' | 'internal'
  source_level: 'primary' | 'secondary' | 'tertiary'
  credibility: 'A' | 'B' | 'C' | 'D'
  relevance_score: number
  published_at?: string | null
  fetched_at?: string
  excluded_by_user?: boolean
  created_at?: string
  updated_at?: string
}

// 冲突（对齐 ConflictResponse；M2-2 起 type 为 critic 四值强枚举）
export interface MockConflict {
  id: string
  run_id: string
  claim: string
  evidence_a_id: string
  evidence_b_id: string
  type: ConflictType
  severity: 'low' | 'medium' | 'high'
  status: 'detected' | 'awaiting_human' | 'resolved' | 'abandoned'
  created_at?: string
  updated_at?: string
}

// 报告引用项（对齐 ReportCitationItem，GET /reports/{id}/citations）
export interface MockCitation {
  evidence_id: string
  marker: string
  snippet: string
  url: string
  title: string
  domain?: string
  source_type?: MockEvidence['source_type']
  source_level?: MockEvidence['source_level']
  credibility?: MockEvidence['credibility']
  published_at?: string | null
}

// 结构化区块（对齐 ReportBlock；M2-7 WP-16 消费）
export interface MockReportBlock {
  id?: string
  type: 'conclusion' | 'evidence' | 'dispute' | 'limitation'
  claim_id?: string
  text: string
  confidence?: 'single_source' | 'cross_verified' | 'inferred'
  citations?: Array<{ evidence_id: string; marker: string; snippet: string }>
  // dispute 区块关联的冲突 id
  conflict_id?: string
}

// 结构化报告（对齐 StructuredReportResponse）
export interface MockStructuredReport {
  id: string
  run_id: string
  status: 'draft' | 'final' | 'superseded'
  outline: Array<{ id: string; title: string; type: string }>
  blocks: MockReportBlock[]
  token_used?: number
  created_at?: string
  updated_at?: string
}

// 剧本执行句柄（与 MockRun 分离，避免随 REST 响应序列化泄漏控制态）
export interface MockExecution {
  script: 'happy_path' | 'demo_full'
  // clarify_pending=澄清挂起等待 resume；running=剧本执行中（可能在 gate 处软暂停）；done/aborted 已结束
  phase: 'clarify_pending' | 'running' | 'done' | 'aborted'
  // 软暂停请求：剧本到下一个 gate 安全点挂起
  pauseRequested: boolean
  // gate 挂起时的解除函数（proceed/resume 调用）
  gateResolver: (() => void) | null
  // 取消请求：gate 立即中止，后续事件一律丢弃
  cancelRequested: boolean
}

// 全局内存态
export const store = {
  users: new Map<string, MockUser>(),
  projects: new Map<string, MockProject>(),
  runs: new Map<string, MockRun>(),
  reportsByRunId: new Map<string, MockReport>(),
  // M2 看板实体（均按 run_id 隔离，保证同一 run 的 REST 与 WS 数据同源）
  stagesByRunId: new Map<string, MockStageRecord[]>(),
  subQuestionsByRunId: new Map<string, MockSubQuestion[]>(),
  evidenceByRunId: new Map<string, MockEvidence[]>(),
  conflictsByRunId: new Map<string, MockConflict[]>(),
  citationsByRunId: new Map<string, MockCitation[]>(),
  structuredReportsByRunId: new Map<string, MockStructuredReport>(),
  // run_id -> 剧本执行句柄
  executions: new Map<string, MockExecution>(),
  // 埋点计数（event 名 -> 累计条数），供 mock 验收观测
  telemetryCounts: new Map<string, number>(),
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
