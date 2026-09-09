"""条件边：根据当前状态决定下一跳节点。"""

from typing import Literal

from app.orchestrator.state import ResearchState


def route_intent(state: ResearchState) -> Literal["clarify", "decompose", "report"]:
    """入口意图分流：根据 raw_query 清晰度决定是否进入澄清环节。"""
    if state.get("clarification_questions"):
        return "clarify"
    if state.get("sub_questions"):
        return "decompose"
    return "decompose"


def decide_after_decompose(state: ResearchState) -> Literal["retrieve", "await_human"]:
    """子问题拆分后：若有子问题则进入检索，否则等待用户确认。"""
    sub_questions = state.get("sub_questions") or []
    return "retrieve" if sub_questions else "await_human"


def decide_after_standardize(state: ResearchState) -> Literal["critique", "report"]:
    """标准化完成后：若存在证据进入审视，否则直接报告。"""
    claims = state.get("standardized_claims") or []
    return "critique" if claims else "report"


def decide_after_critique(
    state: ResearchState,
) -> Literal["retrieve", "report", "user_intervention"]:
    """审视完成后：需要补检索 / 直接出报告 / 触发用户介入。"""
    if state.get("interrupt_requested"):
        return "user_intervention"
    conflicts = state.get("conflicts") or []
    if conflicts and (state.get("cost_used_tokens", 0) < state.get("cost_budget_tokens", 0)):
        return "retrieve"
    return "report"