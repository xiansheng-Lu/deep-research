"""telemetry events

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15

M2-8a 数据埋点：新增 telemetry_events 追加表，承载前端 WP-18 批量上报的
交互观测事件。归属（user_id/team_id）由服务端注入；run_id 仅透传不建外键；
event_ts 为客户端事件时间，created_at 为入库时间。纯新增、无回填。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("team_id", sa.String(length=26), nullable=False),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("page", sa.String(length=128), nullable=True),
        sa.Column("props", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telemetry_events")),
    )
    op.create_index(
        "ix_telemetry_events_created_at", "telemetry_events", ["created_at"]
    )
    op.create_index(
        "ix_telemetry_events_event_time", "telemetry_events", ["event", "created_at"]
    )
    op.create_index("ix_telemetry_events_run", "telemetry_events", ["run_id"])
    op.create_index(
        "ix_telemetry_events_user_time", "telemetry_events", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_events_user_time", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_run", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_event_time", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_created_at", table_name="telemetry_events")
    op.drop_table("telemetry_events")
