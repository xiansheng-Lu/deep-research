// REST 契约类型（M1 对齐 docs/contract/openapi-m1.json；M2 增量对齐 LLD §4.2 与《后端契约草案》v0.1）
// 字段名与后端 FastAPI 响应保持 snake_case 原样，不做驼峰转换；
// 枚举统一从 types/domain 再导出，保证全仓单一事实源（[前端详细设计 §10.7]）。
// M2-9 OpenAPI 导出落地后评估整体切换 openapi-generator typescript-fetch（见 README）。
import type {
  AssistantRole,
  ClaimConfidence,
  ClassifySource,
  ConflictSeverity,
  ConflictStatus,
  ConflictType,
  CostWarningLevel,
  Credibility,
  ForceIntent,
  IntentType,
  InterventionActionType,
  ProjectStatus,
  ReportBlockType,
  ReportStatus as DomainReportStatus,
  ResearchStageName,
  RunStatus,
  RunTier,
  SourceLevel,
  SourceType,
  StageStatus,
  SubQuestionStatus,
  UserActionReason,
  VerdictChoice
} from '@/types/domain'
// M2-5 RunResponse.interrupt 与 interrupt.requested 帧同形（type-only 循环引用，无运行时依赖）
import type { InterruptRequestedPayload } from '@/services/realtime/types'

// ─── 枚举再导出（兼容 M1 既有 import 路径，勿在业务文件直接重复声明）───

export type {
  AssistantRole,
  ClaimConfidence,
  ClassifySource,
  ConflictSeverity,
  ConflictStatus,
  ConflictType,
  CostWarningLevel,
  Credibility,
  ForceIntent,
  IntentType,
  InterventionActionType,
  ProjectStatus,
  ReportBlockType,
  ResearchStageName,
  RunStatus,
  RunTier,
  SourceLevel,
  SourceType,
  StageStatus,
  SubQuestionStatus,
  VerdictChoice
}

// domain 中 ReportStatus 与本文件原 M1 导出同名，统一从 domain 取
export type ReportStatus = DomainReportStatus

// ─── 通用：分页信封（LLD §3 全局契约）───

export interface PageEnvelope<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

// ─── 鉴权（M1）───

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

// ─── 项目（M1）───

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

// ─── 研究运行（M1 + M2 控制）───

// POST /runs 请求体（LLD §4.2.4；clarification_answers 为澄清前置回答，M2 HITL 启用）
export interface CreateRunRequest {
  project_id: string
  question: string
  tier?: RunTier
  template_id?: string | null
  // 意图判为研究且 Clarifier 判需追问时，可随发起请求一并提交（M2）
  clarification_answers?: ClarificationAnswer[]
}

// 澄清问答对（随 POST /runs 提交时的扁平形态）
export interface ClarificationAnswer {
  key: string
  value: string
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
  // M2-5：仅 paused@clarify 非空，其余状态/非澄清挂起恒为 null；服务端不做帧回放，
  // 刷新页面后前端据此恢复澄清卡（交接单 §2.6）
  interrupt?: InterruptRequestedPayload | null
  created_at: string
  updated_at: string
}

// GET /runs/{id}/stages 响应元素（stages 表）
export interface StageResponse {
  id: string
  run_id: string
  name: ResearchStageName
  status: StageStatus
  attempt: number
  started_at?: string | null
  finished_at?: string | null
  created_at?: string
  updated_at?: string
}

// GET /runs/{id}/sub-questions 响应元素（sub_questions 表）
export interface SubQuestionResponse {
  id: string
  run_id: string
  question: string
  depends_on: string[]
  status: SubQuestionStatus
  evidence_count: number
  created_at?: string
  updated_at?: string
}

// GET /runs/{id}/evidence 响应元素（evidence 表；M2-6 信源元数据抽取后字段齐全）
export interface EvidenceResponse {
  id: string
  run_id: string
  sub_question_id: string
  url: string
  domain: string
  title: string
  snippet: string
  // 全文为懒加载字段，列表接口可缺省（[前端详细设计 §9.4]）
  content?: string | null
  source_type: SourceType
  source_level: SourceLevel
  credibility: Credibility
  relevance_score: number
  published_at?: string | null
  fetched_at?: string
  excluded_by_user?: boolean
  created_at?: string
  updated_at?: string
}

// GET /runs/{id}/evidence 查询参数
export interface EvidenceListParams {
  page?: number
  page_size?: number
  sub_question_id?: string
  // 已剔除证据默认不返回；需要恢复列表时显式带 include_excluded
  include_excluded?: boolean
}

// GET /runs/{id}/conflicts 响应元素（conflicts 表）
export interface ConflictResponse {
  id: string
  run_id: string
  claim: string
  evidence_a_id: string
  evidence_b_id: string
  // M2-2 收窄为 critic 四值强枚举（后端 Pydantic Literal；非法值会在标签映射缺键）
  type: ConflictType
  severity: ConflictSeverity
  status: ConflictStatus
  created_at?: string
  updated_at?: string
}

// GET /conflicts/{id} 内嵌的一方证据摘要（后端 FR-7 八项，M2-2 冻结）
export interface ConflictEvidenceSummary {
  id: string
  title: string
  url: string
  domain: string
  snippet: string
  credibility: Credibility
  source_type: SourceType
  published_at?: string | null
}

// GET /conflicts/{id} 响应：列表项字段 + 双方证据内嵌
export interface ConflictDetailResponse extends ConflictResponse {
  evidence_a: ConflictEvidenceSummary
  evidence_b: ConflictEvidenceSummary
}

// GET /runs/{id}/cost/snapshot 响应（字段对齐 cost.warning payload，契约草案 §4.2）
export interface CostSnapshot {
  used: number
  budget: number
  ratio: number
  // M2-4：REST 快照预警级别，与 WS cost.warning 帧同源（<0.7 null / ≥0.7 warning / >0.9 danger）
  level: 'warning' | 'danger' | null
  // 分模型用量明细（M2-3 推送时可能携带；看板成本卡预留不强依赖）
  model_breakdown?: Record<string, number>
}

// GET /runs 查询参数（M2-4 创建者维度列表；status 为六态单值）
export interface RunListParams {
  status?: RunStatus
  project_id?: string
  page?: number
  page_size?: number
}

// ─── HITL：暂停/继续/取消/介入（契约草案 §6.1/§6.2，后端 M2-5 落地）───

// POST /runs/{id}/pause 请求体
export interface PauseRunRequest {
  reason?: UserActionReason | string
}

// POST /runs/{id}/cancel 请求体（keep_partial 默认 true：保留报告草稿）
export interface CancelRunRequest {
  reason?: UserActionReason | string
  keep_partial?: boolean
}

// 裁决答案（嵌套在 HumanInput.answers.verdicts 中，await_human reason=critique 分支）
export interface VerdictAnswer {
  choice: VerdictChoice
  reason: string
}

// POST /conflicts/{id}/verdict 请求体（M2 仅类型对齐，裁决 UI 在 M3）
export interface VerdictRequest {
  choice: VerdictChoice
  reason: string
  additional_note?: string
}

export interface VerdictResponse {
  conflict_id: string
  status: ConflictStatus
  verdict_id: string
}

// 澄清/裁决答案集合：普通问题 key→答案；verdicts 为裁决专用键
export interface HumanInputAnswers {
  [key: string]: string | VerdictAnswer
}

// 主动介入动作（POST /intervene 与 resume {human_input:{action}} 等价）
export interface InterventionAction {
  type: InterventionActionType
  payload: Record<string, unknown>
}

// resume 的统一人类输入模型（三选一：answers / action / kind=proceed）
export interface HumanInput {
  answers?: HumanInputAnswers
  action?: InterventionAction
  // 纯恢复继续（软暂停后），不携带 answers/action
  kind?: 'proceed'
}

// POST /runs/{id}/resume 请求体
export interface ResumeRunRequest {
  human_input?: HumanInput
}

// POST /runs/{id}/intervene 请求体（主动介入快捷通道）
export type InterveneRequest = InterventionAction

// 控制类操作的统一响应
export interface RunControlResponse {
  run_id: string
  status: RunStatus
  // cancel 且报告阶段中断时可能带回草稿报告 id
  partial_report_id?: string
}

// ─── 意图路由（M2-1，backend/app/schemas/intent.py 已落地）───

// POST /intent/classify 请求体
export interface IntentClassifyRequest {
  // 用户原始输入，1-2000 字
  text: string
  // 手动强制路径，缺省走模型自动判别
  force?: ForceIntent
}

// POST /intent/classify 响应体
export interface IntentClassifyResponse {
  intent: IntentType
  confidence: number
  // 研究推荐参数与成本预估：chat 路径为 null
  recommended_template?: string | null
  recommended_tier?: RunTier | null
  estimated_token_budget?: number | null
  estimated_cost_grade?: RunTier | null
  // llm 模型判别 / forced 手动强制 / fallback 保守降级
  source: ClassifySource
  // 保守降级（LLM 不可用/超时）时为 true，前端需显式提示
  degraded: boolean
  reason: string
}

// ─── 闲聊（M2-1，backend/app/schemas/assistant.py；无服务端会话存储）───

export interface AssistantTurn {
  role: AssistantRole
  content: string
}

// POST /assistant/chat 请求体（history 至多 20 条，服务端不落库）
export interface AssistantChatRequest {
  message: string
  history?: AssistantTurn[]
}

// SSE 增量帧解析后的错误形态（event: error）
export interface AssistantStreamError {
  code: string
  message: string
}

// ─── 报告（M1 markdown + M2-7 数据点级溯源结构化）───

// GET /runs/{run_id}/report 与 GET /reports/{report_id} 响应：含 Markdown 正文（M1 形态）
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

// 报告目录项（LLD §4.2.7 outline）
export interface ReportOutlineItem {
  id: string
  title: string
  type: string
}

// 区块内引用（与证据的映射，数据点级溯源依据）
export interface ReportCitation {
  evidence_id: string
  marker: string
  snippet: string
}

// 结构化区块（终稿渲染的唯一允许形态，[前端详细设计 §10.2]）
export interface ReportBlock {
  // 真链终稿恒有值；按冻结契约声明为可选（draft/历史形态可能缺失）
  id?: string
  type: ReportBlockType
  claim_id?: string
  text: string
  confidence?: ClaimConfidence
  citations?: ReportCitation[]
  // dispute 区块关联的冲突 id，由后端确定性注入；缺省时按纯文本分歧块渲染
  conflict_id?: string
}

// GET /reports/{run_id} 与 GET /runs/{run_id}/report 的结构化超集响应
// （M2-7 冻结：原八字段 + outline + blocks；draft/历史报告 outline/blocks 为空数组）
export interface StructuredReportResponse {
  id: string
  run_id: string
  status: ReportStatus
  outline: ReportOutlineItem[]
  blocks: ReportBlock[]
  token_used?: number
  created_at?: string
  updated_at?: string
  // 同端点超集恒回传的 markdown 字段；声明为可选仅为与 ReportResponse 类型解耦
  template_id?: string
  content_md?: string
}

// GET /reports/{report_id}/citations 响应元素：引用 + 证据回溯所需元数据
// （M2-7 冻结；展示字段在 block citation 基础上补全证据来源信息）
export interface ReportCitationItem extends ReportCitation {
  url: string
  title: string
  domain?: string
  source_type?: SourceType
  source_level?: SourceLevel
  credibility?: Credibility
  published_at?: string | null
}

// ─── 埋点（M2 起，[前端详细设计 §16] / 契约草案 §14）───

// 单个埋点事件；props 仅放分类/标识字段，禁止 token、问题原文等敏感内容
export interface TelemetryEvent {
  event: string
  ts: number
  run_id?: string
  page?: string
  props?: Record<string, string | number | boolean | undefined>
}

// POST /telemetry/batch 请求体
export interface TelemetryBatchRequest {
  events: TelemetryEvent[]
}
