"""项目域 Pydantic Schema。

对齐 LLD §6.2 / §8.2；M1 阶段支撑 ``GET /projects`` 与 ``POST /projects`` 两个端点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProjectTier = Literal["quick", "standard", "deep", "extreme"]
ProjectStatus = Literal["active", "archived"]


class CreateProjectRequest(BaseModel):
    """``POST /projects`` 请求体。"""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    default_tier: ProjectTier = Field(default="standard")
    default_template_id: str | None = Field(default=None, max_length=64)


class ProjectResponse(BaseModel):
    """项目视图。"""

    id: str
    team_id: str
    owner_id: str
    name: str
    description: str | None = None
    default_template_id: str | None = None
    default_tier: ProjectTier = "standard"
    status: ProjectStatus = "active"
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CreateProjectRequest",
    "ProjectResponse",
    "ProjectTier",
    "ProjectStatus",
]
