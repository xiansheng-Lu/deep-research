"""澄清节点：当查询模糊时生成澄清问题。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.CLARIFY)
async def run(state: ResearchState) -> dict[str, Any]:
    """澄清占位：M1 阶段由 clarifier 智能体注入问题列表。"""
    return {"clarified_query": state.get("raw_query", "")}