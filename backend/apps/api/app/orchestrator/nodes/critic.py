"""审视节点：识别冲突、生成裁决候选。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.CRITIQUE)
async def run(state: ResearchState) -> dict[str, Any]:
    """审视占位：M1 阶段由 critic 智能体输出 conflicts / verdicts。"""
    return {
        "conflicts": state.get("conflicts") or [],
        "verdicts": state.get("verdicts") or [],
    }