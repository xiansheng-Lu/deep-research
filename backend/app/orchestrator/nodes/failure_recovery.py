"""失败恢复节点：捕获阶段异常后尝试降级或标记失败。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """失败恢复占位：M1 阶段根据 ``failure_reason`` 决定重试 / 降级 / 终态（§6.5.1）。"""
    return {"current_stage": "clarify"}