"""通用 Schema：分页、错误响应、健康检查等。"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

#: 分页参数硬上限：超过由 FastAPI/Pydantic 直接判 422
MAX_PAGE_SIZE = 100


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


class PaginatedResponse(BaseModel, Generic[T]):
    """扁平分页响应（对齐《后端详细设计》§4.1.4）。"""

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1, default=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE, default=20)
    has_more: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)


def build_page(
    items: list[T],
    *,
    total: int,
    page: int,
    page_size: int,
) -> PaginatedResponse[T]:
    """组装分页信封；``has_more`` 由已偏移条数 + 当前页条数推导。"""
    return PaginatedResponse[T](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page - 1) * page_size + len(items) < total,
    )
