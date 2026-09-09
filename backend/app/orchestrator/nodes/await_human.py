"""等待人类节点：阻塞直到用户对澄清问题作答。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """占位：M1 阶段与 user_intervention 配合，通过 LangGraph interrupt 实现。"""
    return {"interrupt_requested": True, "interrupt_reason": "await_human"}