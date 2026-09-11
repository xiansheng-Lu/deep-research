"""冲突域：``Conflict`` 记录证据间的分歧；``Verdict`` 是用户裁决结果。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.run import ResearchRun


class Conflict(Base, IdMixin, TimestampMixin):
    """证据冲突：critical 阶段检测到的证据间分歧，等待人工裁决。"""

    __tablename__ = "conflicts"

    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("research_runs.id"), nullable=False, index=True
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_a_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evidence.id"), nullable=False
    )
    evidence_b_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evidence.id"), nullable=False
    )
    type: Mapped[Literal["factual", "methodological", "temporal", "perspective"]] = (
        mapped_column(String(16), nullable=False)
    )
    severity: Mapped[Literal["low", "medium", "high"]] = mapped_column(
        String(8), nullable=False
    )
    status: Mapped[Literal[
        "detected", "awaiting_human", "resolved", "abandoned"
    ]] = mapped_column(String(16), nullable=False, default="detected")
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Verdict(Base, IdMixin):
    """裁决：用户对冲突的处理决定；1:1 关联 ``Conflict``。"""

    __tablename__ = "verdicts"

    conflict_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("conflicts.id"), nullable=False, unique=True
    )
    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id"), nullable=False
    )
    choice: Mapped[Literal["evidence_a", "evidence_b", "both", "reject"]] = (
        mapped_column(String(16), nullable=False)
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)