"""报告域：``Report`` 记录一次研究产出；``ReportCitation`` 承载数据点级溯源。

M2-7 起 ``ReportCitation`` 为 block×证据 粒度：``block_id`` 关联结构化区块、
``position`` 即报告级 marker 序号（[N]）、``claim_id`` 仅论断块（conclusion/
dispute）携带，evidence/limitation 块为空。
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
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
    """报告引文：区块论断 ↔ 证据的映射，是反查报告来源的依据（M2-7 启用）。"""

    __tablename__ = "report_citations"

    report_id: Mapped[str] = mapped_column(String(26), ForeignKey("reports.id"), nullable=False)
    block_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(26), ForeignKey("evidence.id"), nullable=False)
    # 仅 conclusion/dispute 块携带；evidence/limitation 块为空（0005 起可空）
    claim_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[int] = mapped_column(nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "block_id",
            "evidence_id",
            name="uq_citation_block_evidence",
        ),
        Index("ix_report_citations_report_id", "report_id"),
        Index("ix_citation_claim", "report_id", "claim_id"),
        Index("ix_report_citations_position", "report_id", "position"),
    )
