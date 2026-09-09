"""研究状态图构建入口。

M1 阶段：仅构造 StateGraph 骨架与节点占位；
M1 末：装配所有节点与条件边，暴露 ``compile()`` 返回可执行图。
"""

from langgraph.graph import END, StateGraph

from app.orchestrator.edges import (
    decide_after_critique,
    decide_after_decompose,
    decide_after_standardize,
    route_intent,
)
from app.orchestrator.nodes import (
    await_human,
    clarifier,
    cost_checkpoint,
    critic,
    failure_recovery,
    planner,
    reporter,
    researcher_fan_out,
    standardizer,
    sub_questioner,
    user_intervention,
)
from app.orchestrator.state import ResearchState


def build_research_graph() -> StateGraph:
    """构造研究流程状态图。

    6 阶段主链路 + 治理节点（成本校验、用户介入、等待人类、失败恢复）。
    """
    graph = StateGraph(ResearchState)

    # 节点注册
    graph.add_node("intent_router", planner.intent_router)
    graph.add_node("clarifier", clarifier.run)
    graph.add_node("decompose", sub_questioner.run)
    graph.add_node("retrieve", researcher_fan_out.run)
    graph.add_node("standardize", standardizer.run)
    graph.add_node("critique", critic.run)
    graph.add_node("report", reporter.run)
    graph.add_node("cost_checkpoint", cost_checkpoint.run)
    graph.add_node("user_intervention", user_intervention.run)
    graph.add_node("await_human", await_human.run)
    graph.add_node("failure_recovery", failure_recovery.run)

    # 入口
    graph.set_entry_point("intent_router")

    # 主链路
    graph.add_conditional_edges(
        "intent_router",
        route_intent,
        {"clarify": "clarifier", "decompose": "decompose", "report": "report"},
    )
    graph.add_edge("clarifier", "cost_checkpoint")
    graph.add_edge("cost_checkpoint", "decompose")
    graph.add_conditional_edges(
        "decompose",
        decide_after_decompose,
        {"retrieve": "retrieve", "await_human": "await_human"},
    )
    graph.add_edge("retrieve", "standardize")
    graph.add_conditional_edges(
        "standardize",
        decide_after_standardize,
        {"critique": "critique", "report": "report"},
    )
    graph.add_conditional_edges(
        "critique",
        decide_after_critique,
        {"retrieve": "retrieve", "report": "report", "user_intervention": "user_intervention"},
    )
    graph.add_edge("report", END)
    graph.add_edge("await_human", END)
    graph.add_edge("user_intervention", END)
    graph.add_edge("failure_recovery", END)

    return graph


def compile_research_graph():
    """返回编译后的可执行图对象。"""
    return build_research_graph().compile()