"""成本档位定义与预算解析。"""

from enum import StrEnum

from app.core.config import get_settings


class Tier(StrEnum):
    """研究档位。"""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    EXTREME = "extreme"


def tier_budget(tier: Tier) -> int:
    """返回指定档位的 token 预算上限。"""
    cfg = get_settings()
    return {
        Tier.QUICK: cfg.quota_tier_quick_tokens,
        Tier.STANDARD: cfg.quota_tier_standard_tokens,
        Tier.DEEP: cfg.quota_tier_deep_tokens,
        Tier.EXTREME: cfg.quota_tier_extreme_tokens,
    }[tier]