"""研究链域：``ResearchRun`` 是单次研究活动的根，其下挂载阶段、子问题等实体。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.evidence import Evidence
    from app.db.models.project import Project, User  # noqa: F401


class ResearchRun(Base, IdMixin, TimestampMixin):
    """研究运行：单次研究活动的根节点，承载 LangGraph checkpoint 引用。"""

    __tablename__ = "research_runs"

    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id"), nullable=False, index=True
    )
    creator_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id"), nullable=False
    )
    template_id: Mapped[str] = mapped_column(String(26), nullable=False)
    tier: Mapped[Literal["quick", "standard", "deep", "extreme"]] = mapped_column(
        String(16), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    clarification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[Literal[
        "pending", "running", "paused", "succeeded", "failed", "cancelled"
    ]] = mapped_column(String(16), nullable=False, default="pending")
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    orchestrator_state: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="LangGraph checkpoint 引用"
    )
    token_used: Mapped[int] = mapped_column(nullable=False, default=0)
    token_budget: Mapped[int] = mapped_column(nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")
    stages: Mapped[list[Stage]] = relationship(back_populates="run")
    sub_questions: Mapped[list[SubQuestion]] = relationship(back_populates="run")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="run")

    __table_args__ = (
        Index("ix_runs_project_status", "project_id", "status"),
        Index("ix_runs_creator", "creator_id"),
    )


class Stage(Base, IdMixin, TimestampMixin):
    """研究阶段：6 阶段流水线的执行单元。"""

    __tablename__ = "stages"

    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("research_runs.id"), nullable=False, index=True
    )
    name: Mapped[Literal[
        "clarify", "decompose", "retrieve", "standardize", "critique", "report"
    ]] = mapped_column(String(16), nullable=False)
    status: Mapped[Literal["pending", "running", "succeeded", "failed", "skipped"]] = (
        mapped_column(String(16), nullable=False, default="pending")
    )
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    token_used: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[ResearchRun] = relationship(back_populates="stages")


class SubQuestion(Base, IdMixin, TimestampMixin):
    """子问题：sub_questioner 阶段产物；researcher_fan_out 按子问题并行检索。"""

    __tablename__ = "sub_questions"

    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("research_runs.id"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[Literal[
        "pending", "queued", "running", "succeeded", "failed", "evidence_short"
    ]] = mapped_column(String(16), nullable=False, default="pending")
    evidence_count: Mapped[int] = mapped_column(nullable=False, default=0)

    run: Mapped[ResearchRun] = relationship(back_populates="sub_questions")