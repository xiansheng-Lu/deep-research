"""研究计划智能体（与 intent_router 协同：分流 + 计划骨架）。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段产出 ``sub_questions`` 的初步版本与执行顺序。"""
    return AgentResult(agent="planner")