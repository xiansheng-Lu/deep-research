"""成本校验节点：在关键阶段切换前检查配额。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """占位：检查 ``token_used`` 是否逼近 ``token_budget``。

    M1 阶段在此触发 ``cost.warning`` 事件（§6.5.8）；超预算 90% 由 §6.3 条件边
    ``decide_after_cost_checkpoint`` 路由到 user_intervention。
    """
    used = state.get("token_used", 0)
    budget = state.get("token_budget", 0)
    return {
        "token_used": used,
        "token_budget": budget,
    }