"""身份域：``Team``（租户）与 ``User``（租户成员）。

字段映射 LLD §5.3.1 / §5.3.2；多租户基线（``team_id``）由
``User`` 强制携带，``Team`` 作为隔离根节点。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import DateTime, JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.project import Project


class Team(Base, IdMixin, TimestampMixin):
    """租户：多租户隔离的根节点。"""

    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    plan: Mapped[Literal["free", "pro", "enterprise"]] = mapped_column(
        String(16), nullable=False, default="free"
    )
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    users: Mapped[list[User]] = relationship(back_populates="team")
    projects: Mapped[list[Project]] = relationship(back_populates="team")


class User(Base, IdMixin, TimestampMixin):
    """用户：归属于某个 ``Team``，是研究活动的发起人。"""

    __tablename__ = "users"

    team_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("teams.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[Literal["owner", "admin", "researcher", "reviewer"]] = mapped_column(
        String(16), nullable=False
    )
    # 时间列统一使用 UTC 带时区类型，与 0001 迁移中的 timestamptz 定义对齐
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    team: Mapped[Team] = relationship(back_populates="users")