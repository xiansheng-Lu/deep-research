"""冲突审视域服务函数（M2-2 FR-6/7/8/9）。

路由层只做参数绑定与响应序列化，归属校验、状态机判定、Verdict 落库等
领域逻辑集中在本模块。Task 8 在 ``resume_after_verdict`` 处接线自动恢复。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select

from app.core.exceptions import ConflictError, NotFoundError
from app.db.base import new_ulid
from app.db.models.conflict import Conflict, Verdict
from app.db.models.evidence import Evidence
from app.db.models.run import ResearchRun
from app.realtime.hub import get_hub

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

# 已终结、不允许再提交裁决的冲突状态（FR-9）
_TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "abandoned"})


async def get_owned_run(session: AsyncSession, run_id: str, user_id: str) -> ResearchRun:
    """按创建者归属加载 run；不存在或非本人创建一律 404（FR-6，不泄漏存在性）。"""
    run = await session.scalar(
        select(ResearchRun).where(ResearchRun.id == run_id).where(ResearchRun.creator_id == user_id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")
    return run


async def list_run_conflicts(session: AsyncSession, run_id: str, user_id: str) -> list[Conflict]:
    """列出 run 下全部冲突，按创建时间升序（FR-6）。"""
    await get_owned_run(session, run_id, user_id)
    result = await session.scalars(
        select(Conflict)
        .where(Conflict.run_id == run_id)
        .order_by(Conflict.created_at.asc(), Conflict.id.asc())
    )
    return list(result.all())


async def get_owned_conflict(session: AsyncSession, conflict_id: str, user_id: str) -> Conflict:
    """加载冲突并校验其 run 归属当前用户；不存在/不归属一律 404（FR-7/9）。"""
    conflict = await session.scalar(
        select(Conflict)
        .join(ResearchRun, ResearchRun.id == Conflict.run_id)
        .where(Conflict.id == conflict_id)
        .where(ResearchRun.creator_id == user_id)
    )
    if conflict is None:
        raise NotFoundError("分歧不存在")
    return conflict


async def get_conflict_detail(
    session: AsyncSession, conflict_id: str, user_id: str
) -> tuple[Conflict, Evidence, Evidence]:
    """冲突详情：返回冲突与双方证据 ORM（FR-7 内嵌摘要由 schema 层序列化）。"""
    conflict = await get_owned_conflict(session, conflict_id, user_id)
    evidence_rows = await session.scalars(
        select(Evidence).where(Evidence.id.in_([conflict.evidence_a_id, conflict.evidence_b_id]))
    )
    evidence_by_id = {ev.id: ev for ev in evidence_rows.all()}
    evidence_a = evidence_by_id.get(conflict.evidence_a_id)
    evidence_b = evidence_by_id.get(conflict.evidence_b_id)
    # 证据行缺失属于数据不一致（冲突外键指向不存在的证据），按 404 处理
    if evidence_a is None or evidence_b is None:
        raise NotFoundError("分歧关联的证据不存在")
    return conflict, evidence_a, evidence_b


async def submit_verdict(
    session: AsyncSession,
    *,
    conflict_id: str,
    user_id: str,
    choice: Literal["evidence_a", "evidence_b", "both", "reject"],
    reason: str,
    additional_note: str | None,
) -> tuple[Conflict, Verdict, int]:
    """提交裁决并推进冲突状态机（FR-8/9）。

    - 冲突不存在/不归属 → 404；
    - 冲突已 resolved/abandoned → 409（Verdict 1:1，重复裁决直接拒绝）；
    - 写入 Verdict（含 additional_note），Conflict 置 resolved/resolved_at；
    - 返回 (冲突, 裁决, 同 run 剩余 awaiting_human 数量)，供 Task 8 判定是否恢复。
    """
    conflict = await get_owned_conflict(session, conflict_id, user_id)
    if conflict.status in _TERMINAL_STATUSES:
        raise ConflictError("该分歧已终结，不能重复裁决")

    verdict = Verdict(
        id=new_ulid(),
        conflict_id=conflict.id,
        user_id=user_id,
        choice=choice,
        reason=reason,
        additional_note=additional_note,
    )
    session.add(verdict)
    conflict.status = "resolved"
    conflict.resolved_at = datetime.now(tz=UTC)
    await session.flush()

    remaining = await session.scalar(
        select(func.count())
        .select_from(Conflict)
        .where(Conflict.run_id == conflict.run_id)
        .where(Conflict.status == "awaiting_human")
    )
    return conflict, verdict, int(remaining or 0)


async def resume_after_verdict(
    session: AsyncSession,
    *,
    run_id: str,
    remaining_awaiting: int,
    request: Request | None = None,
) -> None:
    """裁决提交后的自动恢复判定（FR-10 / Task 8）。

    - 仍有 awaiting_human 冲突：run 保持 paused，不调度恢复；
    - 全部裁决完毕：汇总该 run 的全部 Verdict 构造
      ``human_input.answers.verdicts``（暂停期间多条裁决均未进入图状态），
      先提交当前事务（恢复任务用独立会话，必须读到 Verdict/Conflict 行），
      再以后台任务调用 ``resume_research_async`` 从检查点续跑；
    - 无 request 上下文（直接调服务层的测试等）：仅提交，不调度。
    """
    if remaining_awaiting > 0:
        return

    verdict_rows = (
        await session.scalars(
            select(Verdict)
            .join(Conflict, Conflict.id == Verdict.conflict_id)
            .where(Conflict.run_id == run_id)
        )
    ).all()
    answers: dict[str, dict[str, Any]] = {}
    for verdict in verdict_rows:
        answer: dict[str, Any] = {
            "choice": verdict.choice,
            "reason": verdict.reason or "",
            "user_id": verdict.user_id,
        }
        if verdict.additional_note:
            answer["additional_note"] = verdict.additional_note
        answers[verdict.conflict_id] = answer
    human_input = {"answers": {"verdicts": answers}}

    # 恢复任务使用独立会话，必须先让裁决事务对外可见（同 create_run 调度口径）
    await session.commit()

    if request is None:
        return
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return

    # 延迟导入：避免服务层在未装编排依赖的环境（如纯 schema 使用方）强耦合
    from app.orchestrator.executor import resume_research_async

    hub = getattr(request.app.state, "hub", None) or get_hub()
    asyncio.create_task(
        resume_research_async(
            run_id=run_id,
            human_input=human_input,
            session_factory=factory,
            checkpointer=getattr(request.app.state, "checkpointer", None),
            llm=getattr(request.app.state, "llm", None),
            retrieval_client=getattr(request.app.state, "retrieval_client", None),
            hub=hub,
        )
    )


__all__ = [
    "get_conflict_detail",
    "get_owned_conflict",
    "get_owned_run",
    "list_run_conflicts",
    "resume_after_verdict",
    "submit_verdict",
]
