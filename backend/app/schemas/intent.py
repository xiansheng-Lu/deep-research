"""意图路由域 Pydantic Schema（LLD §4.2.8 / 契约草案 §6.6）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IntentType = Literal["chat", "research", "uncertain"]
ForceIntentType = Literal["chat", "research"]
ClassifySource = Literal["llm", "forced", "fallback"]


class IntentClassifyRequest(BaseModel):
    """``POST /intent/classify`` 请求体。"""

    text: str = Field(min_length=1, max_length=2000, description="用户原始输入")
    # 手动强制路径（PRD 模块 G"强制闲聊/强制研究"），缺省走模型自动判别
    force: ForceIntentType | None = Field(default=None)


class IntentClassifyResponse(BaseModel):
    """``POST /intent/classify`` 响应体。

    研究推荐参数与成本预估仅在 research / uncertain 路径有值；
    chat 路径置 None。字段口径对齐 LLD §4.2.8 与契约草案 §6.6。
    """

    intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_template: str | None = None
    recommended_tier: Literal["quick", "standard", "deep", "extreme"] | None = None
    estimated_token_budget: int | None = None
    estimated_cost_grade: Literal["quick", "standard", "deep", "extreme"] | None = None
    # llm 模型判别 / forced 手动强制 / fallback 保守降级
    source: ClassifySource
    # 保守降级（LLM 不可用/超时）时为 True，前端需显式提示用户
    degraded: bool = False
    reason: str = ""


__all__ = [
    "ClassifySource",
    "ForceIntentType",
    "IntentClassifyRequest",
    "IntentClassifyResponse",
    "IntentType",
]
