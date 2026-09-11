"""等待人类节点：阻塞直到用户对澄清问题作答。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """等待人类占位：M1 阶段与 user_intervention 配合，通过 LangGraph interrupt 实现（§6.5.10）。

    恢复后由 §6.3 条件边 ``decide_after_await_human`` 依据 ``interrupt_reason`` 回流
    （clarify → 澄清合并；critique → 裁决收敛）。
    """
    return {"interrupt_reason": state.get("interrupt_reason", "critique")}