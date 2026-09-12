"""意图路由（LLD §4.2.8）：``POST /intent/classify``。

独立于编排图的入口分流接口：研究路径由前端随后调用 ``POST /runs``，
闲聊路径由 ``POST /assistant/chat`` 承接；本接口不落任何项目数据。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.agents.intent_router import classify_intent
from app.api.deps import CurrentUser
from app.schemas.intent import IntentClassifyRequest, IntentClassifyResponse

router = APIRouter(prefix="/intent", tags=["intent"])


@router.post("/classify", response_model=IntentClassifyResponse, summary="判别用户输入意图")
async def classify(
    payload: IntentClassifyRequest,
    request: Request,
    current_user: CurrentUser,
) -> IntentClassifyResponse:
    """判别闲聊 / 研究 / 不确定；LLM 不可用时保守降级为研究。"""
    del current_user  # 仅要求登录态；M2-1 不做用户级个性化
    llm = getattr(request.app.state, "llm", None)
    decision = await classify_intent(payload.text, llm=llm, force=payload.force)
    return IntentClassifyResponse(
        intent=decision.intent,
        confidence=decision.confidence,
        recommended_template=decision.recommended_template,
        recommended_tier=decision.recommended_tier,
        estimated_token_budget=decision.estimated_token_budget,
        estimated_cost_grade=decision.estimated_cost_grade,
        source=decision.source,
        degraded=decision.degraded,
        reason=decision.reason,
    )
