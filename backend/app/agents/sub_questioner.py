"""子问题拆解智能体。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段产出 ``sub_questions`` 列表，每项含 question / priority / dependencies。"""
    return AgentResult(agent="sub_questioner")