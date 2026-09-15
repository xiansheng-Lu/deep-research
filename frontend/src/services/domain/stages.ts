// 研究六阶段顺序常量（openapi-m1：RunResponse.current_stage、WS stage.started）
// 顺序即流水线推进顺序，指挥舱时间线与阶段序号均以此为准
import type { ResearchStageName } from '@/services/api/types'

export const RESEARCH_STAGES: readonly ResearchStageName[] = [
  'clarify',
  'decompose',
  'retrieve',
  'standardize',
  'critique',
  'report'
]

// 阶段一句话描述（指挥舱右列「当前阶段」面板使用，与流水线语义保持一致）
export const STAGE_DESCRIPTIONS: Record<ResearchStageName, string> = {
  clarify: '明确研究边界与关键歧义；信息不足时会向你发起澄清问题。',
  decompose: '把主问题拆解为可独立检索、带依赖关系的子问题。',
  retrieve: '围绕子问题多路检索网页与文档，记录来源、可信度与发布时间。',
  standardize: '清洗去重并结构化证据，统一事实口径供后续审校。',
  critique: '交叉审校不同来源的结论，标记矛盾证据与低可信内容。',
  report: '基于已核验证据撰写带溯源角标的结构化研究报告。'
}
