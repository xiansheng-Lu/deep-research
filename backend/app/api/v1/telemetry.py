"""遥测路由：前端埋点批量接收与指标聚合（M2-8a，契约 §14）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request, Response, status

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import (
    TelemetryBatchInvalidError,
    TelemetryRateLimitedError,
    ValidationError,
)
from app.schemas.telemetry import (
    MAX_BATCH_BYTES,
    MAX_METRICS_WINDOW_DAYS,
    TelemetryBatchRequest,
    TelemetryMetricsResponse,
)
from app.services import telemetry as telemetry_service

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post(
    "/batch",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="前端埋点批量上报",
)
async def post_telemetry_batch(
    request: Request,
    body: TelemetryBatchRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> Response:
    """批量接收埋点：批级结构非法 422、超频 429；采样丢弃/条级非法仍 204。"""
    # 64KB 批体积上限按原始 body 字节二次确认（Pydantic 只校验条数）
    raw = await request.body()
    if len(raw) > MAX_BATCH_BYTES:
        raise TelemetryBatchInvalidError(
            "埋点批体积超过 64KB 上限",
            details={"code": "telemetry_batch_invalid", "max_bytes": MAX_BATCH_BYTES},
        )

    user_id = current_user.id
    if not telemetry_service.check_rate_limit(user_id):
        raise TelemetryRateLimitedError(
            "埋点上报频率超限",
            details={"code": "telemetry_rate_limited"},
        )

    events = [item.model_dump(exclude_none=True) for item in body.events]
    accepted = await telemetry_service.ingest_batch(
        session,
        user_id=user_id,
        team_id=current_user.team_id,
        events=events,
    )
    await session.commit()
    # 204 无体；accepted 仅放调试响应头，前端 fire-and-forget 不依赖
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"X-Accepted-Events": str(accepted)})


@router.get(
    "/metrics",
    response_model=TelemetryMetricsResponse,
    summary="查询 A8 埋点指标窗口聚合",
)
async def get_telemetry_metrics(
    current_user: CurrentUser,
    session: DBSession,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
) -> TelemetryMetricsResponse:
    """全局聚合标量（无明细/无租户维度）；默认近 30 天，窗口上限 90 天。"""
    try:
        frm, end = telemetry_service.parse_window(from_, to)
    except ValueError as exc:
        raise ValidationError(
            str(exc),
            details={"max_days": MAX_METRICS_WINDOW_DAYS},
        ) from exc
    return await telemetry_service.get_metrics(session, frm=frm, to=end)
