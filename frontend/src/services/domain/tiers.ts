// 研究档位元数据（openapi-m1 冻结口径）
// token 预算与后端/mock 档位预算对齐；子问题上限为 M1 向导 UI 提示口径
import type { RunTier } from '@/services/api/types'

export interface TierMeta {
  tier: RunTier
  // 该档位下最多拆解的子问题数（向导/档位卡片提示）
  subQuestionLimit: number
  // token 预算（与 RunResponse.token_budget 对齐）
  tokenBudget: number
}

// 顺序即向导与新建项目对话框中的展示顺序
export const TIER_METAS: TierMeta[] = [
  { tier: 'quick', subQuestionLimit: 3, tokenBudget: 50000 },
  { tier: 'standard', subQuestionLimit: 5, tokenBudget: 150000 },
  { tier: 'deep', subQuestionLimit: 8, tokenBudget: 400000 },
  { tier: 'extreme', subQuestionLimit: 12, tokenBudget: 1000000 }
]

export const TIER_BUDGET: Record<RunTier, number> = {
  quick: 50000,
  standard: 150000,
  deep: 400000,
  extreme: 1000000
}

export function getTierMeta(tier: RunTier): TierMeta {
  return TIER_METAS.find((item) => item.tier === tier) ?? TIER_METAS[1]
}
