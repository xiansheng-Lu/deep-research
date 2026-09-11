"""报告路由：按 run_id 查询报告。

对齐 LLD §6.2 / §8.2；M1 阶段交付 1 个端点：
- ``GET /reports/{run_id}``：按研究运行 ID 查询报告

与 ``GET /runs/{run_id}/report`` 功能等价，提供顶级路由入口便于前端直接引用。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.schemas.reports import ReportResponse

router = APIRouter(prefix="/reports", tags=["reports"])


def _to_response(report: Report) -> ReportResponse:
    return ReportResponse.model_validate(report)


@router.get(
    "/{run_id}",
    response_model=ReportResponse,
    summary="按研究运行 ID 查询报告",
)
async def get_report_by_run(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ReportResponse:
    """按研究运行 ID 查询报告。

    先校验 run 归属当前用户，再查 Report 行。
    """
    run = await session.scalar(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .where(ResearchRun.creator_id == current_user.id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")

    report = await session.scalar(
        select(Report).where(Report.run_id == run_id)
    )
    if report is None:
        raise ValidationError("报告尚未生成")
    return _to_response(report)


__all__ = ["router"]
