"""用户介入队列域：``RunIntervention`` 记录 running 中的主动介入动作（M2-5）。"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

InterventionType = Literal["ask_followup", "exclude_evidence"]
InterventionStatus = Literal["pending", "applied", "rejected"]


class RunIntervention(Base, IdMixin, TimestampMixin):
    """主动介入请求（队列与留档合一）。

    - ask_followup：retrieve 超步边界由 fan-out 消费，动态追加补查层；
    - exclude_evidence：入队即同步落 ``Evidence.excluded_by_user``（看板立即
      生效），行本身记 applied 供 state 侧在同超步剔除。
    """

    __tablename__ = "run_interventions"

    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("research_runs.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 客户端 Idempotency-Key 透传；同一 run 内唯一，为空时不强制幂等
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_run_intervention_idempotency", "run_id", "idempotency_key", unique=True),)
