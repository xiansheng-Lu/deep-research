"""看板查询域服务函数（M2-4）。

路由层只做参数绑定与响应序列化；归属校验复用
``app.services.conflicts.get_owned_run``（不存在/非创建者统一 404，
不泄漏资源存在性）。所有列表排序口径以《M2-3/M2-4 技术方案》§6 为准。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.core.exceptions import NotFoundError
from app.db.models.evidence import Evidence
from app.db.models.project import Project
from app.db.models.run import ResearchRun, Stage, SubQuestion
from app.orchestrator.persistence import STAGE_ORDER
from app.services.conflicts import get_owned_run

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: 排序用的阶段名→序号映射（STAGE_ORDER 是唯一事实源）
_STAGE_ORDER_INDEX: dict[str, int] = {name: i for i, name in enumerate(STAGE_ORDER)}


async def list_creator_runs(
    session: AsyncSession,
    *,
    user_id: str,
    team_id: str,
    status: str | None = None,
    project_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ResearchRun], int]:
    """创建者维度分页查询 run 列表（created_at DESC, id ASC）。

    - 强制 ``creator_id=user_id``；可选六态单值 ``status`` 过滤；
    - 提供 ``project_id`` 时先校验项目属于当前团队，非本团队项目 404
      （与创建端点同一不泄漏口径）。
    """
    filters = [ResearchRun.creator_id == user_id]
    if status is not None:
        filters.append(ResearchRun.status == status)
    if project_id is not None:
        project = await session.scalar(
            select(Project.id).where(Project.id == project_id).where(Project.team_id == team_id)
        )
        if project is None:
            raise NotFoundError("项目不存在或不属于当前团队")
        filters.append(ResearchRun.project_id == project_id)

    total = int(await session.scalar(select(func.count()).select_from(ResearchRun).where(*filters)) or 0)
    rows = list(
        (
            await session.scalars(
                select(ResearchRun)
                .where(*filters)
                .order_by(ResearchRun.created_at.desc(), ResearchRun.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return rows, total


async def list_run_stages(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: str,
) -> list[Stage]:
    """按固定六阶段顺序返回阶段行；历史无阶段行的 run 返回空列表。"""
    await get_owned_run(session, run_id, user_id)
    rows = list((await session.scalars(select(Stage).where(Stage.run_id == run_id))).all())
    # 固定顺序在内存排序：DB 层无序号列，STAGE_ORDER 是顺序唯一事实源；
    # 非预期阶段名排到末尾（防御，理论上被枚举约束）
    rows.sort(key=lambda r: _STAGE_ORDER_INDEX.get(r.name, len(_STAGE_ORDER_INDEX)))
    return rows


async def list_run_sub_questions(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: str,
) -> list[SubQuestion]:
    """返回 run 下全部子问题（created_at ASC, id ASC）。"""
    await get_owned_run(session, run_id, user_id)
    return list(
        (
            await session.scalars(
                select(SubQuestion)
                .where(SubQuestion.run_id == run_id)
                .order_by(SubQuestion.created_at.asc(), SubQuestion.id.asc())
            )
        ).all()
    )


async def list_run_evidence(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    sub_question_id: str | None = None,
    include_excluded: bool = False,
) -> tuple[list[Evidence], int]:
    """证据池分页查询（fetched_at DESC, id ASC）。

    - 默认剔除 ``excluded_by_user=true``，``include_excluded=True`` 带出；
    - ``sub_question_id`` 过滤时校验该子问题归属同一 run，否则 404；
    """
    await get_owned_run(session, run_id, user_id)
    filters = [Evidence.run_id == run_id]
    if sub_question_id is not None:
        owned_sq = await session.scalar(
            select(SubQuestion.id)
            .where(SubQuestion.id == sub_question_id)
            .where(SubQuestion.run_id == run_id)
        )
        if owned_sq is None:
            raise NotFoundError("子问题不存在")
        filters.append(Evidence.sub_question_id == sub_question_id)
    if not include_excluded:
        filters.append(Evidence.excluded_by_user.is_(False))

    total = int(await session.scalar(select(func.count()).select_from(Evidence).where(*filters)) or 0)
    rows = list(
        (
            await session.scalars(
                select(Evidence)
                .where(*filters)
                .order_by(Evidence.fetched_at.desc(), Evidence.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return rows, total


async def get_run_evidence(
    session: AsyncSession,
    *,
    run_id: str,
    evidence_id: str,
    user_id: str,
) -> Evidence:
    """单条证据详情（全文懒加载通道）；归属不符/不存在统一 404。"""
    await get_owned_run(session, run_id, user_id)
    evidence = await session.scalar(
        select(Evidence).where(Evidence.id == evidence_id).where(Evidence.run_id == run_id)
    )
    if evidence is None:
        raise NotFoundError("证据不存在")
    return evidence


async def get_cost_snapshot(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: str,
) -> tuple[ResearchRun, float]:
    """读取成本快照：``(run 行, 用量占比)``；level 由 schema 侧同源派生。"""
    run = await get_owned_run(session, run_id, user_id)
    budget = int(run.token_budget)
    ratio = (int(run.token_used) / budget) if budget > 0 else 0.0
    return run, ratio


__all__ = (
    "list_creator_runs",
    "list_run_stages",
    "list_run_sub_questions",
    "list_run_evidence",
    "get_run_evidence",
    "get_cost_snapshot",
)
