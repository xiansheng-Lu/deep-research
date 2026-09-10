"""审计域：``AuditEntry`` 记录所有用户行为与系统事件，供合规追溯。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin


class AuditEntry(Base, IdMixin):
    """审计条目：不可变记录（应用层只追加、不更新/删除）。"""

    __tablename__ = "audit_entries"

    team_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, index=True)