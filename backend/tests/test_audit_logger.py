"""审计写入器离线测试（AC-12 后半：写入失败不阻断业务动作）。"""

from __future__ import annotations

from typing import Any

from app.audit.base import AuditAction
from app.audit.logger import write_audit_entry


class _BrokenNested:
    """模拟 begin_nested 即失败（如 savepoint 不可用）的会话。"""

    def begin_nested(self) -> Any:
        raise RuntimeError("savepoint 不可用")


async def test_write_audit_entry_failure_is_swallowed() -> None:
    """savepoint 抛错时 write_audit_entry 只告警不外抛，业务动作不被阻断。"""
    await write_audit_entry(
        _BrokenNested(),  # type: ignore[arg-type]
        team_id="team-1",
        user_id="user-1",
        action=AuditAction.RUN_PAUSE,
        target_type="run",
        target_id="run-1",
        payload={"reason": "x"},
    )
