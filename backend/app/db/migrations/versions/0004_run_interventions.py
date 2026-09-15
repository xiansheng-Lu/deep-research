"""run interventions

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14

M2-5 用户介入：新增 run_interventions 表承载 running 中主动介入动作
（ask_followup/exclude_evidence）；(run_id, idempotency_key) 唯一索引支撑
Idempotency-Key 自然幂等（idempotency_key 可空，PG 唯一索引中 NULL 互不相等）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_interventions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_interventions_run_id", "run_interventions", ["run_id"])
    op.create_index(
        "ix_run_intervention_idempotency",
        "run_interventions",
        ["run_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_run_intervention_idempotency", table_name="run_interventions")
    op.drop_index("ix_run_interventions_run_id", table_name="run_interventions")
    op.drop_table("run_interventions")
