"""冲突审视路由（M2-2 FR-6/7/8/9）。

三端点：
- ``GET  /runs/{run_id}/conflicts``：run 下分歧列表（按创建时间，需归属）
- ``GET  /conflicts/{conflict_id}``：分歧详情（内嵌双方证据摘要八项）
- ``POST /conflicts/{conflict_id}/verdict``：提交裁决（409 重复裁决）

错误语义：401（未认证）/404（不存在或非创建者）/422（choice 枚举、reason 非空）
/409（冲突已终结）。裁决后的自动恢复由 Task 8 经服务钩子接线。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.conflicts import (
    ConflictDetailResponse,
    ConflictEvidenceSummary,
    ConflictResponse,
    VerdictRequest,
    VerdictResponse,
)
from app.services import conflicts as conflict_service

router = APIRouter(tags=["conflicts"])


@router.get(
    "/runs/{run_id}/conflicts",
    response_model=list[ConflictResponse],
    summary="查询研究运行下的分歧列表",
)
async def list_run_conflicts(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[ConflictResponse]:
    """返回 run 下全部分歧，按创建时间升序；非创建者 404（FR-6）。"""
    rows = await conflict_service.list_run_conflicts(session, run_id, current_user.id)
    return [ConflictResponse.model_validate(row) for row in rows]


@router.get(
    "/conflicts/{conflict_id}",
    response_model=ConflictDetailResponse,
    summary="查询分歧详情（含双方证据摘要）",
)
async def get_conflict(
    conflict_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ConflictDetailResponse:
    """返回冲突字段及双方证据摘要；不存在或不归属返回 404（FR-7）。"""
    conflict, evidence_a, evidence_b = await conflict_service.get_conflict_detail(
        session, conflict_id, current_user.id
    )
    return ConflictDetailResponse(
        **ConflictResponse.model_validate(conflict).model_dump(),
        evidence_a=ConflictEvidenceSummary.model_validate(evidence_a),
        evidence_b=ConflictEvidenceSummary.model_validate(evidence_b),
    )


@router.post(
    "/conflicts/{conflict_id}/verdict",
    response_model=VerdictResponse,
    status_code=status.HTTP_200_OK,
    summary="提交分歧裁决",
)
async def submit_conflict_verdict(
    conflict_id: str,
    payload: VerdictRequest,
    current_user: CurrentUser,
    session: DBSession,
    request: Request,
) -> VerdictResponse:
    """写入 Verdict 并把 Conflict 置 resolved（FR-8/9）。

    已终结冲突返回 409；请求级事务由 db_session 依赖在响应后统一提交。
    提交后的自动恢复判定经服务钩子执行（Task 8 接线）。
    """
    conflict, verdict, remaining = await conflict_service.submit_verdict(
        session,
        conflict_id=conflict_id,
        user_id=current_user.id,
        choice=payload.choice,
        reason=payload.reason,
        additional_note=payload.additional_note,
    )
    # Task 8 钩子：剩余 awaiting_human 清零时后台恢复图（本任务为空操作）
    await conflict_service.resume_after_verdict(
        session,
        run_id=conflict.run_id,
        remaining_awaiting=remaining,
        request=request,
    )
    return VerdictResponse(
        conflict_id=conflict.id,
        status=conflict.status,
        verdict_id=verdict.id,
    )


__all__ = ["router"]
