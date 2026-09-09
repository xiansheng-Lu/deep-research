"""审计日志写入占位：M1 阶段落 ORM（AuditEntry 表）。"""

from app.audit.base import AuditEntry


class AuditLogger:
    """审计日志门面占位。"""

    def __init__(self) -> None:
        self._buffer: list[AuditEntry] = []

    async def write(self, entry: AuditEntry) -> None:
        self._buffer.append(entry)
        # M1 阶段替换为 ORM 写入 + 异步批量刷盘
        _ = entry

    def _unused(self) -> None:
        _ = self._buffer