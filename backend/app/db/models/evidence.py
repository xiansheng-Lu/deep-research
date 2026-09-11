"""证据域：``Evidence`` 记录一次检索到的网页证据及其可信度元数据。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.run import ResearchRun, SubQuestion


class Evidence(Base, IdMixin, TimestampMixin):
    """证据：检索阶段产物，可信度由 ``source_level`` / ``credibility`` 标注。"""

    __tablename__ = "evidence"

    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("research_runs.id"), nullable=False, index=True
    )
    sub_question_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("sub_questions.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_storage_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="MinIO key"
    )
    source_type: Mapped[Literal[
        "official_doc", "news", "community", "search", "internal"
    ]] = mapped_column(String(16), nullable=False)
    source_level: Mapped[Literal["primary", "secondary", "tertiary"]] = mapped_column(
        String(16), nullable=False
    )
    credibility: Mapped[Literal["A", "B", "C", "D"]] = mapped_column(
        String(1), nullable=False
    )
    relevance_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="URL + content hash 去重"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    excluded_by_user: Mapped[bool] = mapped_column(nullable=False, default=False)

    run: Mapped[ResearchRun] = relationship(back_populates="evidence")

    __table_args__ = (
        Index("ix_evidence_run_subq", "run_id", "sub_question_id"),
    )