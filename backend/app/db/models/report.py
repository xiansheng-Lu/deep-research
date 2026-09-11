"""报告域：``Report`` 记录一次研究产出的 Markdown 报告；``ReportCitation`` 关联证据。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Report(Base, IdMixin, TimestampMixin):
    """研究产出报告：reporter 阶段产物；1:1 关联 ``ResearchRun``。"""

    __tablename__ = "reports"

    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("research_runs.id"), nullable=False, unique=True
    )
    template_id: Mapped[str] = mapped_column(String(26), nullable=False)
    status: Mapped[Literal["draft", "final", "superseded"]] = mapped_column(
        String(16), nullable=False, default="draft"
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    token_used: Mapped[int] = mapped_column(nullable=False, default=0)


class ReportCitation(Base, IdMixin):
    """报告引文：声明 ↔ 证据的映射，是反查报告来源的依据。"""

    __tablename__ = "report_citations"

    report_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("reports.id"), nullable=False, index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evidence.id"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_citation_claim", "report_id", "claim_id"),)