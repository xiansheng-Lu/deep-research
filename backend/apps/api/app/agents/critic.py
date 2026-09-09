"""冲突识别与裁决候选智能体。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段产出 ``conflicts`` / ``verdicts``，供人类裁决使用。"""
    return AgentResult(agent="critic")