"""verdict additional note

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12

M2-2 批判收敛：verdicts 增加 additional_note（可空团队协作备注，仅用于
报告局限区块引用与展示，不参与 Critic 推理，对齐《后端契约草案》§6.3）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("verdicts", sa.Column("additional_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("verdicts", "additional_note")
