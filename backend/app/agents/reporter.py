"""报告组装智能体。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段产出 ``report_outline`` / ``report_markdown``，支持流式分片。

    流式产出通过 ``realtime.sse`` 推送到前端。
    """
    return AgentResult(agent="reporter")