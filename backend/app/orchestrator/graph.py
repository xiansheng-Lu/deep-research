"""研究状态图构建入口（装配语义对齐《后端详细设计》§6.3）。

M0 阶段：仅构造 StateGraph 骨架与节点占位；
M1 末：接入 AsyncPostgresSaver checkpointer 与 interrupt_before（见 §6.3 注记）。
"""

from langgraph.graph import END, START, StateGraph

from app.orchestrator.edges import (
    decide_after_await_human,
    decide_after_clarify,
    decide_after_cost_checkpoint,
    decide_after_critique,
)
from app.orchestrator.nodes import (
    await_human,
    clarifier,
    cost_checkpoint,
    critic,
    failure_recovery,
    reporter,
    researcher_fan_out,
    standardizer,
    sub_questioner,
    user_intervention,
)
from app.orchestrator.state import ResearchState


def build_research_graph() -> StateGraph:
    """构造研究流程状态图（§6.3）。

    主链路：START → failure_recovery → clarify → decompose → retrieve → standardize
    → critique → cost_checkpoint → report → END；
    旁路：clarify/critique 经 await_human HITL 挂起回流；cost_checkpoint 超支经
    user_intervention 介入后并入 report。
    """
    graph = StateGraph(ResearchState)

    # 节点注册（intent_router 不入图，见 §6.3 对齐说明）
    graph.add_node("failure_recovery", failure_recovery.run)
    graph.add_node("clarify", clarifier.run)
    graph.add_node("decompose", sub_questioner.run)
    graph.add_node("retrieve", researcher_fan_out.run)
    graph.add_node("standardize", standardizer.run)
    graph.add_node("critique", critic.run)
    graph.add_node("report", reporter.run)
    graph.add_node("cost_checkpoint", cost_checkpoint.run)
    graph.add_node("user_intervention", user_intervention.run)
    graph.add_node("await_human", await_human.run)

    # 入口与主链路
    graph.add_edge(START, "failure_recovery")
    graph.add_edge("failure_recovery", "clarify")
    # 阶段1 澄清：若 clarifier 判定需用户追问，则挂起到 await_human；用户回答后回流重新并入
    graph.add_conditional_edges(
        "clarify",
        decide_after_clarify,
        {"await_human": "await_human", "decompose": "decompose"},
    )
    graph.add_edge("decompose", "retrieve")
    graph.add_edge("retrieve", "standardize")
    graph.add_edge("standardize", "critique")

    # 审视：存在未裁决分歧则挂起 await_human（interrupt_reason="critique" 回流收敛），否则进入成本闸门
    graph.add_conditional_edges(
        "critique",
        decide_after_critique,
        {"await_human": "await_human", "cost_checkpoint": "cost_checkpoint"},
    )
    # await_human 为统一 HITL 挂起点：按 interrupt_reason 回流（clarify → 澄清合并；critique → 裁决收敛）
    graph.add_conditional_edges(
        "await_human",
        decide_after_await_human,
        {"clarify": "clarify", "critique": "critique"},
    )
    # 成本闸门：token 用量超预算 90% 触发用户介入，否则进入报告
    graph.add_conditional_edges(
        "cost_checkpoint",
        decide_after_cost_checkpoint,
        {"user_intervention": "user_intervention", "report": "report"},
    )
    graph.add_edge("user_intervention", "report")
    graph.add_edge("report", END)

    return graph


def compile_research_graph():
    """返回编译后的可执行图对象。

    M1 阶段在此接入 AsyncPostgresSaver 并传 ``interrupt_before=["await_human", "user_intervention"]``。
    """
    return build_research_graph().compile()
