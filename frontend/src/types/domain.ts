// 领域枚举单一事实源（[前端详细设计 §10.7]）
// 与后端枚举值一一对应，取值以后端代码/契约为准：
// - run/stage/sub_question/evidence/conflict：backend/app/db/models 与 orchestrator/state.py
// - intent/assistant：backend/app/schemas/intent.py、assistant.py（M2-1 已落地）
// - HITL：《后端契约草案》v0.1 §6
// 展示文案统一在 services/i18n/zh-CN.ts 映射，本文件只声明类型，不含中文。

// ─── run / 档位 ───

// 研究档位（CreateRunRequest.tier / ProjectResponse.default_tier）
export type RunTier = 'quick' | 'standard' | 'deep' | 'extreme'

// run 生命周期状态（RunResponse.status；paused=澄清/裁决挂起，非失败终态）
export type RunStatus = 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'

// 研究六阶段（state.py ResearchStage），顺序即流水线推进顺序
export type ResearchStageName =
  | 'clarify'
  | 'decompose'
  | 'retrieve'
  | 'standardize'
  | 'critique'
  | 'report'

// 阶段执行状态（stages 表：无 running 之外的在途态；skipped 为降级跳过）
export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'

// ─── 子问题 / 证据 / 冲突 ───

// 子问题状态（sub_questions 表；evidence_short=证据不足）
export type SubQuestionStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'evidence_short'

// 信源类型（evidence.source_type）
export type SourceType = 'official_doc' | 'news' | 'community' | 'search' | 'internal'

// 信源层级（evidence.source_level）
export type SourceLevel = 'primary' | 'secondary' | 'tertiary'

// 可信分级（evidence.credibility；A 最高 D 最低）
export type Credibility = 'A' | 'B' | 'C' | 'D'

// 冲突状态（conflicts 表）
export type ConflictStatus = 'detected' | 'awaiting_human' | 'resolved' | 'abandoned'

// 冲突严重度（conflict.severity）
export type ConflictSeverity = 'low' | 'medium' | 'high'

// 冲突类型（conflicts.type；critic 四分类，与后端 schemas/conflicts.py 一致，M2-2 冻结）
export type ConflictType = 'factual' | 'methodological' | 'temporal' | 'perspective'

// 裁决选择（契约草案 §6.3；M2 仅类型对齐，裁决 UI 在 M3）
export type VerdictChoice = 'evidence_a' | 'evidence_b' | 'both' | 'reject'

// ─── 报告 ───

// 报告状态（reports 表；superseded=被重生成替换，M4 产生）
export type ReportStatus = 'draft' | 'final' | 'superseded'

// 结构化区块类型（LLD §4.2.7）
export type ReportBlockType = 'conclusion' | 'evidence' | 'dispute' | 'limitation'

// 论断置信度（report_claims.confidence）
export type ClaimConfidence = 'single_source' | 'cross_verified' | 'inferred'

// 项目状态（projects 表）
export type ProjectStatus = 'active' | 'archived'

// ─── 意图路由（M2-1，backend/app/schemas/intent.py）───

// 意图三分类：闲聊 / 研究 / 不确定
export type IntentType = 'chat' | 'research' | 'uncertain'

// 手动强制路径（PRD 模块 G）
export type ForceIntent = 'chat' | 'research'

// 判别来源：模型判别 / 手动强制 / 保守降级
export type ClassifySource = 'llm' | 'forced' | 'fallback'

// 成本预警级别（cost.warning payload，契约草案 §4.2）
export type CostWarningLevel = 'warning' | 'danger'

// ─── 闲聊（M2-1，backend/app/schemas/assistant.py）───

export type AssistantRole = 'user' | 'assistant'

// ─── HITL 介入（契约草案 §6.2）───

// 主动介入动作类型（revert_stage/mark_doubt 为 M4，M2 仅声明不用）
export type InterventionActionType =
  | 'ask_followup'
  | 'exclude_evidence'
  | 'revert_stage'
  | 'mark_doubt'

// M2 实际开放的介入动作子集
export const M2_INTERVENTION_ACTIONS = new Set<InterventionActionType>([
  'ask_followup',
  'exclude_evidence'
])

// 介入请求原因（pause/cancel 请求体，写入审计）
export type UserActionReason = 'user_action'
