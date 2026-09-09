"""澄清问题生成智能体。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段生成澄清问题列表与澄清后查询。

    返回 ``clarification_questions`` 与 ``clarified_query`` 字段。
    """
    return AgentResult(agent="clarifier")