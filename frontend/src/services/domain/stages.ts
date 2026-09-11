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
