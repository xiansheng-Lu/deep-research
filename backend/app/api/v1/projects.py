"""项目路由：项目列表与创建。

对齐 LLD §6.2 / §8.2；M1 阶段交付 2 个端点：
- ``GET  /projects``：列出当前用户所属团队下 ``active`` 项目（不含已归档/已删除）
- ``POST /projects``：创建项目，``owner_id`` 取当前用户，``team_id`` 取当前用户所属团队

租户隔离：所有查询 / 创建均以 ``current_user.team_id`` 为边界。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import NotFoundError
from app.db.models.project import Project
from app.schemas.projects import CreateProjectRequest, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_response(project: Project) -> ProjectResponse:
    return ProjectResponse.model_validate(project)


@router.get("", response_model=list[ProjectResponse], summary="列出当前团队的项目")
async def list_projects(
    current_user: CurrentUser, session: DBSession
) -> list[ProjectResponse]:
    """列出当前用户所属团队下 ``status='active'`` 且未软删除的项目。"""
    stmt = (
        select(Project)
        .where(Project.team_id == current_user.team_id)
        .where(Project.status == "active")
        .where(Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc())
    )
    result = await session.scalars(stmt)
    projects = list(result.all())
    return [_to_response(p) for p in projects]


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建项目",
)
async def create_project(
    payload: CreateProjectRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """创建项目并归属到当前用户及其团队。"""
    now = datetime.now(tz=UTC)
    project = Project(
        team_id=current_user.team_id,
        owner_id=current_user.id,
        name=payload.name,
        description=payload.description,
        default_template_id=payload.default_template_id,
        default_tier=payload.default_tier,
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    await session.flush()
    if project.id is None:  # pragma: no cover - 防御性兜底
        raise NotFoundError("项目创建失败")
    return _to_response(project)


__all__ = ["router"]
