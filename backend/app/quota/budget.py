"""配额检查占位：M1 阶段实现按 Run / 按团队的预算扣减与告警。"""

from app.quota.tiers import Tier, tier_budget


def reserve_budget(tier: Tier, used: int) -> bool:
    """占位：检查已用 token 是否仍在预算内。"""
    return used <= tier_budget(tier)