"""遥测域：``TelemetryEvent`` 记录前端批量上报的埋点事件（M2-8a）。

追加表（只 INSERT、不更新/删除）：归属由服务端从登录态注入，不信任请求中的
user_id/team_id；``event_ts`` 为客户端 epoch 毫秒时间，``created_at`` 为入库
时间。观测旁路通道，不参与任何业务状态写路径。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin


class TelemetryEvent(Base, IdMixin):
    """前端埋点事件（不可变追加）。"""

    __tablename__ = "telemetry_events"

    # 服务端注入：事件归属，请求体携带也忽略
    user_id: Mapped[str] = mapped_column(String(26), nullable=False)
    team_id: Mapped[str] = mapped_column(String(26), nullable=False)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    # 客户端透传的 run 关联，仅格式校验、不建外键（观测旁路，缺行不阻塞入库）
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page: Mapped[str | None] = mapped_column(String(128), nullable=True)
    props: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 客户端事件时间（epoch ms 转换）
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 服务端入库时间（追加表无 updated_at）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
