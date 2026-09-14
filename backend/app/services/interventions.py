"""用户主动介入队列服务（M2-5）：入队校验与 fan-out 边界消费助手。

- ``submit_intervention``：running 中 ask_followup（仅 retrieve）/
  exclude_evidence（retrieve/standardize/critique）的归属、阶段、幂等校验；
- ``pending_interventions`` / ``mark_applied``：fan-out 分层循环边界消费；
- ``excluded_evidence_ids``：critic/reporter 结论链读取剔除集合。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.base import AuditAction
from app.audit.logger import write_audit_entry
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.base import new_ulid
from app.db.models.evidence import Evidence
from app.db.models.intervention import RunIntervention
from app.db.models.run import ResearchRun, SubQuestion
from app.schemas.runs import InterventionAction

log = get_logger("services.interventions")

#: 允许各介入动作的运行阶段
_ASK_STAGES = ("retrieve",)
_EXCLUDE_STAGES = ("retrieve", "standardize", "critique")

_QUESTION_MAX_LEN = 1000
_REASON_MAX_LEN = 256


async def submit_intervention(
    session: AsyncSession,
    *,
    run: ResearchRun,
    user_id: str,
    team_id: str,
    action: InterventionAction,
    idempotency_key: str | None,
) -> ResearchRun:
    """受理一条主动介入；返回当前 run（响应统一为 RunControlResponse）。"""
    if str(run.status) != "running":
        raise ConflictError(
            "当前研究运行状态不允许介入操作",
            details={"code": "INTERVENE_NOT_ALLOWED"},
        )

    stage = str(run.current_stage or "")
    payload = dict(action.payload or {})
    reason = payload.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > _REASON_MAX_LEN):
        raise ValidationError("reason 须为不超过 256 字的字符串")

    if action.type == "ask_followup":
        await _validate_ask(session, run_id=run.id, stage=stage, payload=payload)
    else:
        await _validate_and_apply_exclude(session, run_id=run.id, stage=stage, payload=payload)

    # 幂等：同 (run_id, key) 重复请求直接成功返回，不重复入队/执行
    if idempotency_key:
        existing = await session.scalar(
            select(RunIntervention).where(
                RunIntervention.run_id == run.id,
                RunIntervention.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            log.info(
                "介入请求命中幂等键",
                extra={"run_id": run.id, "idempotency_key": idempotency_key},
            )
            return run

    status = "applied" if action.type == "exclude_evidence" else "pending"
    intervention = RunIntervention(
        id=new_ulid(),
        run_id=run.id,
        user_id=user_id,
        type=action.type,
        payload=payload,
        status=status,
        idempotency_key=idempotency_key,
    )
    session.add(intervention)
    await write_audit_entry(
        session,
        team_id=team_id,
        user_id=user_id,
        action=(
            AuditAction.INTERVENE_ASK_FOLLOWUP
            if action.type == "ask_followup"
            else AuditAction.INTERVENE_EXCLUDE_EVIDENCE
        ),
        target_type="run",
        target_id=run.id,
        payload=payload,
    )
    run.updated_at = datetime.now(tz=UTC)
    await session.commit()
    log.info(
        "介入请求已受理",
        extra={"run_id": run.id, "type": action.type, "status": status},
    )
    return run


async def _validate_ask(
    session: AsyncSession,
    *,
    run_id: str,
    stage: str,
    payload: dict[str, Any],
) -> None:
    if stage not in _ASK_STAGES:
        raise ConflictError(
            "追加追问仅在检索阶段可用",
            details={"code": "INTERVENE_NOT_ALLOWED"},
        )
    sub_question_id = payload.get("sub_question_id")
    question = payload.get("question")
    if not isinstance(sub_question_id, str) or not sub_question_id.strip():
        raise ValidationError("ask_followup 需要非空字符串字段 sub_question_id")
    if not isinstance(question, str) or not question.strip() or len(question) > _QUESTION_MAX_LEN:
        raise ValidationError(f"question 须为 1~{_QUESTION_MAX_LEN} 字非空字符串")
    subq = await session.scalar(
        select(SubQuestion).where(
            SubQuestion.id == sub_question_id,
            SubQuestion.run_id == run_id,
        )
    )
    if subq is None:
        raise NotFoundError("子问题不存在")


async def _validate_and_apply_exclude(
    session: AsyncSession,
    *,
    run_id: str,
    stage: str,
    payload: dict[str, Any],
) -> None:
    if stage not in _EXCLUDE_STAGES:
        raise ConflictError(
            "剔除证据仅在检索/标准化/审视阶段可用",
            details={"code": "INTERVENE_NOT_ALLOWED"},
        )
    evidence_id = payload.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValidationError("exclude_evidence 需要非空字符串字段 evidence_id")
    result = await session.execute(
        update(Evidence)
        .where(Evidence.id == evidence_id, Evidence.run_id == run_id)
        .values(excluded_by_user=True, updated_at=datetime.now(tz=UTC))
    )
    if cast(CursorResult[Any], result).rowcount == 0:
        raise NotFoundError("证据不存在")


async def pending_interventions(
    session: AsyncSession,
    run_id: str,
) -> list[RunIntervention]:
    """读取本 run 待消费的介入动作（fan-out 层边界调用，按创建时间稳定排序）。"""
    rows = await session.scalars(
        select(RunIntervention)
        .where(
            RunIntervention.run_id == run_id,
            RunIntervention.status == "pending",
        )
        .order_by(RunIntervention.created_at, RunIntervention.id)
    )
    return list(rows.all())


async def mark_applied(session: AsyncSession, intervention_id: str) -> None:
    """将介入动作标记为已应用（fan-out 消费后调用）。"""
    await session.execute(
        update(RunIntervention)
        .where(RunIntervention.id == intervention_id)
        .values(status="applied", updated_at=datetime.now(tz=UTC))
    )


async def excluded_evidence_ids(session: AsyncSession, run_id: str) -> set[str]:
    """读取本 run 被用户剔除的证据 id 集合（结论链过滤用）。"""
    rows = await session.scalars(
        select(Evidence.id).where(
            Evidence.run_id == run_id,
            Evidence.excluded_by_user.is_(True),
        )
    )
    return set(rows.all())


__all__ = [
    "submit_intervention",
    "pending_interventions",
    "mark_applied",
    "excluded_evidence_ids",
]
