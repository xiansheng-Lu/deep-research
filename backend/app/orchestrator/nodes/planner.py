"""意图路由节点：根据原始查询决定起点。"""

from typing import Any

from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import ResearchStage, ResearchState


@instrument(ResearchStage.DECOMPOSE)
async def intent_router(state: ResearchState) -> dict[str, Any]:
    """意图路由占位：实际实现由 planner 智能体提供（M1 末）。

    当前仅返回空 patch；M1 阶段接通后会在此处填充 ``route_hint`` 等字段。
    """
    return {}