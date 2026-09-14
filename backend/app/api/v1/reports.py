"""报告路由：按 run_id 查询报告与信源索引。

M2-7 交付 2 个端点：
- ``GET /reports/{run_id}``：报告（八字段 + 结构化 outline/blocks 超集）
- ``GET /reports/{run_id}/citations``：报告级信源索引（marker 升序去重）

路径参数历史命名即 run_id（run↔report 1:1），与前端实参传 runId 的调用约定一致。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.schemas.reports import ReportCitationItem, ReportResponse
from app.services import reports as reports_service

router = APIRouter(prefix="/reports", tags=["reports"])


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
    """按研究运行 ID 查询报告（含 M2-7 结构化终稿 outline/blocks）。"""
    report = await reports_service.get_report_for_run(session, run_id=run_id, user_id=current_user.id)
    return reports_service.to_response(report)


@router.get(
    "/{run_id}/citations",
    response_model=list[ReportCitationItem],
    summary="查询报告的数据点级溯源信源索引",
)
async def get_report_citations(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[ReportCitationItem]:
    """报告级信源列表：角标 [N] + 原文片段 + URL/可信元数据，按 marker 升序去重。"""
    return await reports_service.list_citations(session, run_id=run_id, user_id=current_user.id)


__all__ = ["router"]
