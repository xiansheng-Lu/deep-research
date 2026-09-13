"""条件边：根据当前状态决定下一跳节点（语义对齐《后端详细设计》§6.3）。"""

from typing import Literal

from app.orchestrator.state import ResearchState
from app.quota.tiers import cost_danger_ratio


def decide_after_clarify(state: ResearchState) -> Literal["await_human", "decompose"]:
    """澄清完成后：若 clarifier 判定需用户追问则挂起到 await_human，否则进入子问题拆解。"""
    if state.get("needs_clarification"):
        return "await_human"
    return "decompose"


def decide_after_critique(state: ResearchState) -> Literal["await_human", "cost_checkpoint"]:
    """审视完成后：仍存在「待裁决且无对应 Verdict」的 high 冲突才挂起。

    M2-2 门控（FR-4）：low/medium 自动收敛的冲突状态为 resolved，不参与挂起；
    多条 high 冲突逐条裁决时，只要还有一条没有对应 verdict 就继续挂起；
    全部裁决后回流不再挂起，进入成本闸门。
    """
    conflicts = state.get("conflicts") or []
    verdict_ids = {v["conflict_id"] for v in (state.get("verdicts") or [])}
    has_pending = any(
        c.get("status") == "awaiting_human" and c.get("id") not in verdict_ids for c in conflicts
    )
    if has_pending:
        return "await_human"
    return "cost_checkpoint"


def decide_after_await_human(state: ResearchState) -> Literal["clarify", "critique"]:
    """await_human 恢复后：按 interrupt_reason 回流（clarify → 澄清合并；critique → 裁决收敛）。"""
    if state.get("interrupt_reason") == "clarify":
        return "clarify"
    return "critique"


def decide_after_cost_checkpoint(state: ResearchState) -> Literal["user_intervention", "report"]:
    """成本闸门：token 用量超 danger 阈值（默认 90%）触发用户介入，否则进入报告生成。

    阈值与 WS ``cost.warning`` 发射器共用 ``app.quota.tiers.cost_danger_ratio``，
    保证前端成本卡变红与本挂起路由同源（M2-4 §5.2）。
    """
    used = state.get("token_used", 0)
    budget = state.get("token_budget", 0)
    if used > budget * cost_danger_ratio():
        return "user_intervention"
    return "report"
