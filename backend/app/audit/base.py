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
    # M2-5：软暂停/恢复/硬取消与两类主动介入
    RUN_PAUSE = "run.pause"
    RUN_RESUME = "run.resume"
    RUN_CANCEL = "run.cancel"
    INTERVENE_ASK_FOLLOWUP = "intervene.ask_followup"
    INTERVENE_EXCLUDE_EVIDENCE = "intervene.exclude_evidence"
    REPORT_EXPORT = "report.export"
    CONNECTOR_BIND = "connector.bind"
    CONNECTOR_SYNC = "connector.sync"
    # M2-8b：worker 启动孤儿清扫收敛（paused/succeeded/failed 三类）
    RUN_ORPHAN_RECOVERED = "run.orphan_recovered"


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
