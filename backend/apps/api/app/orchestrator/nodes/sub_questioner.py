"""子问题拆解节点。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.DECOMPOSE)
async def run(state: ResearchState) -> dict[str, Any]:
    """子问题占位：M1 阶段由 sub_questioner 智能体填充列表。"""
    return {"sub_questions": state.get("sub_questions") or []}