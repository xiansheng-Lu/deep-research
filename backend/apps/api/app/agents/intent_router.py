"""意图路由智能体。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位实现：M1 阶段根据 raw_query 决定起点（clarify / decompose / report）。

    当前返回空 patches；M1 末接入 LLM 与 prompt 模板后填充实际决策。
    """
    return AgentResult(agent="intent_router")