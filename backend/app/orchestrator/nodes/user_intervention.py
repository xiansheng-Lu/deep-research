"""用户介入节点：通过 LangGraph interrupt 暂停并等待人类反馈。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """占位：M1 阶段调用 LangGraph 的 ``interrupt`` 机制暂停执行。"""
    return {
        "interrupt_requested": True,
        "interrupt_reason": state.get("interrupt_reason"),
    }