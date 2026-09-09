"""智能体集合。

8 个智能体 + 通用 AgentContext：
    intent_router  入口意图分流
    clarifier      澄清问题生成
    planner        研究计划编排（与 intent_router 协同）
    sub_questioner 子问题拆解
    researcher     单点检索（含并发 fan-out 包装）
    standardizer   证据 → 结构化声明
    critic         冲突识别与裁决候选
    reporter       报告组装
"""

from app.agents.base import AgentContext, AgentResult

__all__ = ["AgentContext", "AgentResult"]