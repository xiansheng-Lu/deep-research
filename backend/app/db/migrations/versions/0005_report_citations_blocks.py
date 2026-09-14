"""report citations blocks

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14

M2-7 数据点级溯源：启用 0001 已建但从未写入的 report_citations 表，将其调整为
block×证据 粒度——
- 新增 block_id（String(64) NOT NULL），关联结构化区块；
- claim_id 改可空（evidence/limitation 块无 claim 标识）；
- 新增 (report_id, block_id, evidence_id) 唯一约束，块内引用不重复；
- 新增 (report_id, position) 索引，信源索引按 marker 序号排序读取。

该表建成后无任何写入点（空表），故直接改结构、不做数据回填；
downgrade 反向拆除后 claim_id 恢复 NOT NULL（空表回退安全）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_citations", sa.Column("block_id", sa.String(length=64), nullable=False))
    op.alter_column(
        "report_citations",
        "claim_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.create_unique_constraint(
        "uq_citation_block_evidence",
        "report_citations",
        ["report_id", "block_id", "evidence_id"],
    )
    op.create_index(
        "ix_report_citations_position",
        "report_citations",
        ["report_id", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_citations_position", table_name="report_citations")
    op.drop_constraint("uq_citation_block_evidence", "report_citations", type_="unique")
    op.alter_column(
        "report_citations",
        "claim_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_column("report_citations", "block_id")
