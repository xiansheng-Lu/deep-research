"""报告生成节点。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.REPORT)
async def run(state: ResearchState) -> dict[str, Any]:
    """报告占位：M1 阶段由 reporter 智能体流式产出 report_markdown。"""
    return {"report_markdown": state.get("report_markdown") or ""}