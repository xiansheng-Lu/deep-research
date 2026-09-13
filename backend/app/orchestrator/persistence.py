"""过程数据持久化（M2-2 / M2-4）：阶段 / 子问题 / 证据从图 state 幂等落库。

设计口径：

- ``Stage`` run 启动补齐固定六阶段 pending 行，随执行推进做状态迁移；
  (run_id,name) 唯一约束（0003）保证 upsert 幂等、恢复重放不产生重复行。
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
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_ulid
from app.db.models.conflict import Conflict
from app.db.models.evidence import Evidence
from app.db.models.run import Stage, SubQuestion

#: 阶段行状态取值（与 Stage 模型 Mapped 字面量保持一致）
StageStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]

# 固定六阶段顺序：看板时间线与 ensure_stage_rows 补齐顺序的唯一事实源
STAGE_ORDER: tuple[str, ...] = (
    "clarify",
    "decompose",
    "retrieve",
    "standardize",
    "critique",
    "report",
)


def _parse_dt(value: str | None) -> datetime | None:
    """ISO 字符串转带时区 datetime；空值返回 None。"""
    return datetime.fromisoformat(value) if value else None


def apply_stage_transition(
    row: Stage,
    *,
    status: StageStatus | None = None,
    attempt: int | None = None,
    token_used: int | None = None,
    finished: bool = False,
    error_code: str | None = None,
    now: datetime | None = None,
) -> Stage:
    """对内存中的阶段行应用一次状态迁移规则。

    规则（M2-4 §8）：

    - 已 succeeded 的行不回退（恢复重放命中已完成阶段时原样保留）；
    - 进入 running 且 started_at 为空时写开始时间（恢复时沿用旧值）；
    - finished=True 时置 finished_at，调用方须同时给终态 status；
    - failed 时写 error_code。
    """
    if row.status == "succeeded":
        return row
    moment = now or datetime.now(tz=UTC)
    if status is not None:
        row.status = status
    if attempt is not None:
        row.attempt = int(attempt)
    if row.status == "running" and row.started_at is None:
        row.started_at = moment
    if finished:
        row.finished_at = moment
    if token_used is not None:
        row.token_used = int(token_used)
    if error_code is not None:
        row.error_code = error_code
    row.updated_at = moment
    return row


async def ensure_stage_rows(session: AsyncSession, *, run_id: str) -> dict[str, Stage]:
    """补齐 run 的固定六阶段行（缺失的插 pending，已存在的保留），返回名称→行映射。

    幂等：run 首次启动时插入六行；恢复路径再次调用只取回既有行，不重置任何
    已推进的状态。返回字典便于执行器在流式循环中零查询更新阶段行。
    """
    rows = list((await session.scalars(select(Stage).where(Stage.run_id == run_id))).all())
    by_name: dict[str, Stage] = {row.name: row for row in rows}
    now = datetime.now(tz=UTC)
    for name in STAGE_ORDER:
        if name in by_name:
            continue
        row = Stage(
            id=new_ulid(),
            run_id=run_id,
            name=name,
            status="pending",
            attempt=1,
            started_at=None,
            finished_at=None,
            token_used=0,
            error_code=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        by_name[name] = row
    return by_name


async def persist_stage_transition(
    session: AsyncSession,
    *,
    run_id: str,
    name: str,
    status: StageStatus | None = None,
    attempt: int | None = None,
    token_used: int | None = None,
    finished: bool = False,
    error_code: str | None = None,
) -> Stage:
    """按 (run_id,name) upsert 单个阶段行的状态迁移，返回迁移后的行。

    行不存在时插入（兼容直接从恢复路径进入、缺行的场景）；行存在时套用
    ``apply_stage_transition`` 规则。执行器热路径持有 ``ensure_stage_rows``
    的行映射时直接改对象即可，不必走本函数的查询。
    """
    row = await session.scalar(select(Stage).where(Stage.run_id == run_id).where(Stage.name == name))
    if row is None:
        now = datetime.now(tz=UTC)
        row = Stage(
            id=new_ulid(),
            run_id=run_id,
            name=name,
            status=status or "running",
            attempt=int(attempt or 1),
            started_at=now if (status or "running") == "running" else None,
            finished_at=now if finished else None,
            token_used=int(token_used or 0),
            error_code=error_code,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        return row
    return apply_stage_transition(
        row,
        status=status,
        attempt=attempt,
        token_used=token_used,
        finished=finished,
        error_code=error_code,
    )


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
) -> list[Evidence]:
    """按 ID 插入缺失证据（幂等），返回本次新增的 ORM 行列表。

    ``items`` 接受 ``EvidenceDict`` 列表或普通 dict 列表（单测构造用）。
    返回新增行（已 ``session.add``、未 flush），调用方据此逐条发射
    ``evidence.fetched`` 实时帧——已存在的证据不视为"本次抓取"，不发帧。
    """
    if not items:
        return []
    existing = set((await session.scalars(select(Evidence.id).where(Evidence.run_id == run_id))).all())
    added: list[Evidence] = []
    for ev in items:
        ev_id = ev["id"]
        if ev_id in existing:
            continue
        row = Evidence(
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
        session.add(row)
        added.append(row)
        existing.add(ev_id)
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


__all__ = [
    "STAGE_ORDER",
    "apply_stage_transition",
    "ensure_stage_rows",
    "persist_stage_transition",
    "persist_sub_questions",
    "persist_evidence",
    "persist_conflicts",
]
