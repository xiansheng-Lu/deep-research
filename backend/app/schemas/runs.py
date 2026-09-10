"""研究执行域 Pydantic Schema。

对齐 LLD §6.2 / §8.2；M1 阶段支撑 ``POST /runs`` / ``GET /runs/{id}`` /
``GET /runs/{id}/report`` 三个端点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RunTier = Literal["quick", "standard", "deep", "extreme"]
RunStatus = Literal[
    "pending", "running", "paused", "succeeded", "failed", "cancelled"
]


class CreateRunRequest(BaseModel):
    """``POST /runs`` 请求体。"""

    project_id: str = Field(min_length=1, max_length=26)
    question: str = Field(min_length=5, max_length=4096)
    tier: RunTier = Field(default="standard")
    template_id: str | None = Field(default=None, max_length=64)


class RunResponse(BaseModel):
    """研究运行视图。"""

    id: str
    project_id: str
    creator_id: str
    template_id: str
    tier: RunTier
    question: str
    status: RunStatus = "pending"
    current_stage: str | None = None
    token_used: int = 0
    token_budget: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    stream_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CreateRunRequest",
    "RunResponse",
    "RunTier",
    "RunStatus",
]
