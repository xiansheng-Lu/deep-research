"""意图路由实现单元（不入编排图）。

对齐 HLD §7.1：意图路由能力属 API 层独立接口（§4.2.8 ``/intent/classify``，M2 接线），
不作为 LangGraph 图节点；M2 落地时独立为 ``nodes/intent_router.py`` 供该接口复用。
"""

from typing import Any

from app.orchestrator.state import ResearchState


async def intent_router(state: ResearchState) -> dict[str, Any]:
    """意图路由占位：实际实现由 intent_router 智能体提供（M2 末）。

    当前仅返回空 patch；M2 接通后会在此处填充 ``route_hint`` 等字段。
    """
    return {}