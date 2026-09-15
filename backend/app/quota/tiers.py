"""成本档位定义、预算解析与成本预警阈值。

阈值的运行时唯一事实源是 ``Settings``（env 可调），模块级常量为默认值锚点；
条件边（``edges.decide_after_cost_checkpoint``）与实时成本发射器
（``app.quota.emitter.RunCostEmitter``）必须经本模块访问函数取阈值，
禁止在调用点重写 0.70/0.90 魔数，保证"成本卡变红"与"90% 挂起闸门"同源。
"""

from enum import StrEnum
from typing import Final, Literal

from app.core.config import get_settings

#: cost.warning/cost 快照级别的可空取值
CostLevel = Literal["warning", "danger"]


class Tier(StrEnum):
    """研究档位。"""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    EXTREME = "extreme"


# 成本预警阈值默认值（Settings 同名字段的 default 锚点，修改需同步 config）
COST_WARNING_RATIO: Final[float] = 0.70
COST_DANGER_RATIO: Final[float] = 0.90


def tier_budget(tier: Tier) -> int:
    """返回指定档位的 token 预算上限。"""
    cfg = get_settings()
    return {
        Tier.QUICK: cfg.quota_tier_quick_tokens,
        Tier.STANDARD: cfg.quota_tier_standard_tokens,
        Tier.DEEP: cfg.quota_tier_deep_tokens,
        Tier.EXTREME: cfg.quota_tier_extreme_tokens,
    }[tier]


def cost_warning_ratio() -> float:
    """cost.warning=warning 的实时阈值（默认 0.70，env 可覆盖）。"""
    return get_settings().cost_warning_ratio


def cost_danger_ratio() -> float:
    """cost.warning=danger 阈值兼 90% 挂起闸门（默认 0.90，env 可覆盖）。"""
    return get_settings().cost_danger_ratio


def classify_cost_level(
    ratio: float,
    *,
    warning_ratio: float | None = None,
    danger_ratio: float | None = None,
) -> CostLevel | None:
    """按阈值派生成本级别：``ratio > danger`` → danger；``≥ warning`` → warning。

    阈值缺省走 Settings（实时帧与 cost/snapshot 同源于本函数，禁止各处自算）。
    边界口径与 ``RunCostEmitter`` 边沿状态机一致：danger 为严格大于。
    """
    warning = cost_warning_ratio() if warning_ratio is None else warning_ratio
    danger = cost_danger_ratio() if danger_ratio is None else danger_ratio
    if ratio > danger:
        return "danger"
    if ratio >= warning:
        return "warning"
    return None
