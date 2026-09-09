"""证据 → 结构化声明智能体。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段产出 ``standardized_claims``，每条声明带 citation 引用。"""
    return AgentResult(agent="standardizer")