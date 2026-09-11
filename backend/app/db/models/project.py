"""项目域：``Project`` 是研究活动的归属边界，所有研究链实体向上汇聚到项目。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.identity import Team, User
    from app.db.models.run import ResearchRun


class Project(Base, IdMixin, TimestampMixin):
    """项目：研究主题的容器，归属于 ``Team``，由 ``User`` 创建。"""

    __tablename__ = "projects"

    team_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("teams.id"), nullable=False, index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    default_template_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    default_tier: Mapped[Literal["quick", "standard", "deep", "extreme"]] = mapped_column(
        String(16), nullable=False, default="standard"
    )
    status: Mapped[Literal["active", "archived"]] = mapped_column(
        String(16), nullable=False, default="active"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    team: Mapped[Team] = relationship(back_populates="projects")
    runs: Mapped[list[ResearchRun]] = relationship(back_populates="project")

    __table_args__ = (Index("ix_projects_team_status", "team_id", "status"),)