"""检索节点：并行 fan-out 调用 researcher 智能体。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.RETRIEVE)
async def run(state: ResearchState) -> dict[str, Any]:
    """检索占位：M1 阶段接入 tavily / 自有检索，并对每个子问题并发执行。"""
    return {"evidence": state.get("evidence") or []}