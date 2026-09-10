"""报告域 Pydantic Schema。

对齐 LLD §6.2 / §8.2；M1 阶段支撑 ``GET /runs/{id}/report`` 与
``GET /reports/{run_id}`` 两个端点（前者为 runs 子路由，后者为 reports 顶级路由）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ReportStatus = Literal["draft", "final", "superseded"]


class ReportResponse(BaseModel):
    """报告视图：含 Markdown 正文。"""

    id: str
    run_id: str
    template_id: str
    status: ReportStatus = "draft"
    content_md: str
    token_used: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = ["ReportResponse", "ReportStatus"]
