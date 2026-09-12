"""过程数据持久化（M2-2）：子问题 / 证据从图 state 幂等落库。

设计口径：

- ``SubQuestion`` 按 ID upsert——decompose 创建（初始状态）、retrieve 回写
  执行状态与证据 ID、standardize 因跨子问题去重收敛证据引用，三个时点共用；
- ``Evidence`` 按 ID 仅插入缺失：证据内容不可变，落库时点统一在 standardize
  分类之后，避免把 retrieve 阶段的临时分级（tertiary/C）固化；
- ``Conflict`` 按 ID 仅插入缺失：critic 检出即落库，low/medium 自动收敛写
  resolved（resolved_at，无 Verdict 行），high 写 awaiting_human 等裁决。
- 恢复重放安全：同一 run 重复执行不产生重复行。外键顺序要求子问题先于证据
  写入，由节点顺序（decompose→retrieve→standardize）天然保证。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conflict import Conflict
from app.db.models.evidence import Evidence
from app.db.models.run import SubQuestion


def _parse_dt(value: str | None) -> datetime | None:
    """ISO 字符串转带时区 datetime；空值返回 None。"""
    return datetime.fromisoformat(value) if value else None


async def persist_sub_questions(
    session: AsyncSession,
    *,
    run_id: str,
    items: Sequence[Mapping[str, Any]],
) -> int:
    """按 ID upsert 子问题，返回触达条数。

    ``items`` 接受 ``SubQuestionDict`` 列表或普通 dict 列表（单测构造用）。
    """
    if not items:
        return 0
    ids = [sq["id"] for sq in items]
    rows = list((await session.scalars(select(SubQuestion).where(SubQuestion.id.in_(ids)))).all())
    by_id = {row.id: row for row in rows}
    touched = 0
    for sq in items:
        evidence_count = len(sq.get("evidence_ids") or [])
        row = by_id.get(sq["id"])
        if row is None:
            session.add(
                SubQuestion(
                    id=sq["id"],
                    run_id=run_id,
                    question=sq["question"],
                    depends_on=list(sq.get("depends_on") or []),
                    status=sq["status"],
                    evidence_count=evidence_count,
                )
            )
        else:
            row.question = sq["question"]
            row.depends_on = list(sq.get("depends_on") or [])
            row.status = sq["status"]
            row.evidence_count = evidence_count
        touched += 1
    return touched


async def persist_evidence(
    session: AsyncSession,
    *,
    run_id: str,
    items: Sequence[Mapping[str, Any]],
) -> int:
    """按 ID 插入缺失证据（幂等），返回新增条数。

    ``items`` 接受 ``EvidenceDict`` 列表或普通 dict 列表（单测构造用）。
    """
    if not items:
        return 0
    existing = set((await session.scalars(select(Evidence.id).where(Evidence.run_id == run_id))).all())
    added = 0
    for ev in items:
        ev_id = ev["id"]
        if ev_id in existing:
            continue
        session.add(
            Evidence(
                id=ev_id,
                run_id=run_id,
                sub_question_id=ev["sub_question_id"],
                url=ev.get("url") or "",
                domain=ev.get("domain") or "",
                title=ev.get("title") or "",
                snippet=ev.get("snippet") or "",
                content=None,
                source_type=ev.get("source_type") or "search",
                source_level=ev.get("source_level") or "tertiary",
                credibility=ev.get("credibility") or "D",
                relevance_score=float(ev.get("relevance_score") or 0.0),
                fingerprint=ev["fingerprint"],
                published_at=_parse_dt(ev.get("published_at")),
                fetched_at=_parse_dt(ev.get("fetched_at")) or datetime.now(tz=UTC),
            )
        )
        existing.add(ev_id)
        added += 1
    return added


async def persist_conflicts(
    session: AsyncSession,
    *,
    run_id: str,
    items: Sequence[Mapping[str, Any]],
) -> int:
    """按 ID 插入缺失冲突（幂等），返回新增条数。

    状态口径（FR-3 / FR-4）：

    - ``resolved``：low/medium 自动收敛的冲突，落库即写 ``resolved_at``，
      不写 Verdict 行（无人工裁决）；
    - ``awaiting_human``：high 冲突挂起等待裁决，``resolved_at`` 为空。

    恢复重放安全：同 ID 冲突只插入一次；Verdict 行由裁决 REST 端点写入，
    不在本函数职责内。
    """
    if not items:
        return 0
    existing = set((await session.scalars(select(Conflict.id).where(Conflict.run_id == run_id))).all())
    now = datetime.now(tz=UTC)
    added = 0
    for c in items:
        conflict_id = c["id"]
        if conflict_id in existing:
            continue
        status = str(c.get("status") or "detected")
        session.add(
            Conflict(
                id=conflict_id,
                run_id=run_id,
                claim=str(c.get("claim") or ""),
                evidence_a_id=c["evidence_a_id"],
                evidence_b_id=c["evidence_b_id"],
                type=str(c.get("type") or "factual"),
                severity=str(c.get("severity") or "medium"),
                status=status,
                resolved_at=now if status == "resolved" else None,
            )
        )
        existing.add(conflict_id)
        added += 1
    return added


__all__ = ["persist_sub_questions", "persist_evidence", "persist_conflicts"]
