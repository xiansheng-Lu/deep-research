"""run execution lease

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15

M2-8b 长任务接管：research_runs 增执行租约观测列（execution_owner/lease_until）
与 (status, lease_until) 部分扫描索引。权威互斥仍在 Redis 租约，本两列仅为
worker 启动孤儿清扫的扫描输入与人工排查镜像；历史 run 两列留 NULL，不回填。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("execution_owner", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "research_runs",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_status_lease", "research_runs", ["status", "lease_until"])


def downgrade() -> None:
    op.drop_index("ix_runs_status_lease", table_name="research_runs")
    op.drop_column("research_runs", "lease_until")
    op.drop_column("research_runs", "execution_owner")
