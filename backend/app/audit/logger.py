"""审计日志写入（M2-5 起承接真实 ORM 落库）。

控制动作（pause/resume/cancel/intervene）在业务事务内经 savepoint 写入
``audit_entries``；审计写入自身失败只告警，不阻断/回滚业务动作。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.audit.base import AuditAction
from app.core.logging import get_logger
from app.db.models.audit import AuditEntry as AuditEntryModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("audit.writer")


async def write_audit_entry(
    session: AsyncSession,
    *,
    team_id: str,
    user_id: str,
    action: AuditAction,
    target_type: str,
    target_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """在业务事务的 savepoint 内追加一条审计记录；失败仅告警不抛。"""
    try:
        nested = session.begin_nested()
        async with nested:
            session.add(
                AuditEntryModel(
                    team_id=team_id,
                    user_id=user_id,
                    action=str(action),
                    target_type=target_type,
                    target_id=target_id,
                    payload=payload or {},
                    # 模型列无默认值且 0001 建表为 NOT NULL：写入器统一补时间戳
                    created_at=datetime.now(tz=UTC),
                )
            )
    except Exception as exc:  # noqa: BLE001 - 审计可用性不阻断业务动作
        log.warning(
            "审计条目写入失败（不影响业务动作）",
            extra={"action": str(action), "target_id": target_id, "error": repr(exc)},
        )


__all__ = ["write_audit_entry"]
