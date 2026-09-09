"""单点检索智能体（被 researcher_fan_out 节点并发调用）。"""

from app.agents.base import AgentContext, AgentResult


async def run(ctx: AgentContext) -> AgentResult:
    """占位：M1 阶段对单个子问题检索 web / knowledge / connectors，返回 evidence_items。

    节点 ``researcher_fan_out`` 负责并发 fan-out 与结果聚合。
    """
    return AgentResult(agent="researcher")