"""通用 Schema：分页、错误响应、健康检查等。"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = "ok"
    service: str
    version: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """统一错误响应。"""

    code: str
    message: str
    details: dict[str, Any] | None = None
    trace_id: str | None = None


class PageMeta(BaseModel):
    """分页元信息。"""

    page: int = Field(ge=1, default=1)
    page_size: int = Field(ge=1, le=200, default=20)
    total: int = Field(ge=0)


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应。"""

    items: list[T]
    meta: PageMeta

    model_config = ConfigDict(arbitrary_types_allowed=True)