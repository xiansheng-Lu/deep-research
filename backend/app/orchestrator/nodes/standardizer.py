"""标准化节点：将证据汇总为带引用的结构化声明。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.STANDARDIZE)
async def run(state: ResearchState) -> dict[str, Any]:
    """标准化占位：M1 阶段由 standardizer 智能体产出 standardized_evidence。"""
    return {"standardized_evidence": state.get("standardized_evidence") or []}