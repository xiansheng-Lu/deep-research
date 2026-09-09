"""审计日志公共数据结构。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class AuditAction(StrEnum):
    """审计动作枚举。"""

    LOGIN = "login"
    LOGOUT = "logout"
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    RUN_START = "run.start"
    RUN_INTERRUPT = "run.interrupt"
    RUN_COMPLETE = "run.complete"
    REPORT_EXPORT = "report.export"
    CONNECTOR_BIND = "connector.bind"
    CONNECTOR_SYNC = "connector.sync"


@dataclass(slots=True)
class AuditEntry:
    """单条审计记录。"""

    id: UUID
    actor_id: UUID
    team_id: UUID
    action: AuditAction
    target_type: str
    target_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None