"""条件边：根据当前状态决定下一跳节点（语义对齐《后端详细设计》§6.3）。"""

from typing import Literal

from app.orchestrator.state import ResearchState


def decide_after_clarify(state: ResearchState) -> Literal["await_human", "decompose"]:
    """澄清完成后：若 clarifier 判定需用户追问则挂起到 await_human，否则进入子问题拆解。"""
    if state.get("needs_clarification"):
        return "await_human"
    return "decompose"


def decide_after_critique(state: ResearchState) -> Literal["await_human", "cost_checkpoint"]:
    """审视完成后：存在未裁决分歧则挂起 await_human，否则进入成本闸门。"""
    conflicts = state.get("conflicts") or []
    verdicts = state.get("verdicts") or []
    if conflicts and not verdicts:
        return "await_human"
    return "cost_checkpoint"


def decide_after_await_human(state: ResearchState) -> Literal["clarify", "critique"]:
    """await_human 恢复后：按 interrupt_reason 回流（clarify → 澄清合并；critique → 裁决收敛）。"""
    if state.get("interrupt_reason") == "clarify":
        return "clarify"
    return "critique"


def decide_after_cost_checkpoint(state: ResearchState) -> Literal["user_intervention", "report"]:
    """成本闸门：token 用量超预算 90% 触发用户介入，否则进入报告生成。"""
    used = state.get("token_used", 0)
    budget = state.get("token_budget", 0)
    if used > budget * 0.9:
        return "user_intervention"
    return "report"
