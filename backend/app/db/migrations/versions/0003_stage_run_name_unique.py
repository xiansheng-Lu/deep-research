"""stage run_id name unique

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13

M2-4 看板接口：stages 增加 (run_id, name) 唯一约束 uq_stages_run_name，
作为阶段行 upsert 幂等与恢复重放的前置。存量 stages 为空表，升级无冲突。
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_stages_run_name", "stages", ["run_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_stages_run_name", "stages", type_="unique")
