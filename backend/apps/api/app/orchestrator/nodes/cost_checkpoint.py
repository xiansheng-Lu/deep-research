"""成本校验节点：在关键阶段切换前检查配额。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """占位：检查 ``cost_used_tokens`` 是否逼近 ``cost_budget_tokens``。

    M1 阶段在此触发 ``cost.warning`` 事件并设置 ``interrupt_requested``。
    """
    used = state.get("cost_used_tokens", 0)
    budget = state.get("cost_budget_tokens", 0)
    return {
        "cost_used_tokens": used,
        "cost_budget_tokens": budget,
    }