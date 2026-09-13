"""``app.orchestrator.persistence`` 单元测试（M2-2 Task 3）。

不依赖真实 Postgres：以最小 FakeSession 捕获 add 的 ORM 对象，并依据 select
目标表返回预置行，验证幂等 upsert 与必填字段组装。列级默认值（excluded_by_user
等）的真实库断言见 tests/test_integration_persistence.py。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_ulid
from app.db.models.conflict import Conflict
from app.db.models.evidence import Evidence
from app.db.models.run import Stage, SubQuestion
from app.orchestrator.persistence import (
    STAGE_ORDER,
    apply_stage_transition,
    ensure_stage_rows,
    persist_conflicts,
    persist_evidence,
    persist_stage_transition,
    persist_sub_questions,
)


def _as_session(fake: Any) -> AsyncSession:
    """鸭子类型的内存假会话到 AsyncSession 的静态桥（仅类型层 cast）。"""
    return cast(AsyncSession, fake)


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """记录 add 调用；按 select 目标表返回预置行，模拟同事务内可见性。"""

    def __init__(
        self,
        *,
        existing_subs: list[SubQuestion] | None = None,
        existing_evidence_ids: set[str] | None = None,
        existing_conflict_ids: set[str] | None = None,
        existing_stages: list[Stage] | None = None,
    ) -> None:
        self.added: list[Any] = []
        self._subs: dict[str, SubQuestion] = {s.id: s for s in (existing_subs or [])}
        self._evidence_ids: set[str] = set(existing_evidence_ids or set())
        self._conflict_ids: set[str] = set(existing_conflict_ids or set())
        self._stages: dict[str, Stage] = {s.name: s for s in (existing_stages or [])}

    async def scalars(self, statement: Any) -> _FakeScalars:
        sql = str(statement)
        if "FROM stages" in sql:
            return _FakeScalars(list(self._stages.values()))
        if "FROM sub_questions" in sql:
            return _FakeScalars(list(self._subs.values()))
        if "FROM evidence" in sql:
            return _FakeScalars(sorted(self._evidence_ids))
        if "FROM conflicts" in sql:
            return _FakeScalars(sorted(self._conflict_ids))
        raise AssertionError(f"未预期的查询: {sql}")

    async def scalar(self, statement: Any) -> Stage | None:  # noqa: ARG002
        """persist_stage_transition 的单行查询：假会话统一走缺行插入路径。"""
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, SubQuestion):
            self._subs[obj.id] = obj
        elif isinstance(obj, Evidence):
            self._evidence_ids.add(obj.id)
        elif isinstance(obj, Conflict):
            self._conflict_ids.add(obj.id)
        elif isinstance(obj, Stage):
            self._stages[obj.name] = obj


def _sq(sq_id: str, *, status: str = "pending", evidence_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": sq_id,
        "question": f"子问题-{sq_id}",
        "depends_on": [],
        "status": status,
        "evidence_ids": evidence_ids or [],
    }


def _ev(ev_id: str, *, sub_question_id: str = "sq-1") -> dict[str, Any]:
    return {
        "id": ev_id,
        "sub_question_id": sub_question_id,
        "url": f"https://example.com/{ev_id}",
        "domain": "example.com",
        "title": f"证据标题-{ev_id}",
        "snippet": "证据摘要内容",
        "source_type": "news",
        "source_level": "secondary",
        "credibility": "B",
        "relevance_score": 0.8,
        "fingerprint": f"fp-{ev_id}",
        "published_at": "2026-09-01T00:00:00+00:00",
        "fetched_at": "2026-09-02T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_persist_sub_questions_inserts_then_updates_by_id() -> None:
    """首次按 ID 插入；二次同 ID 调用只更新可变字段，不新增行（TR-3.2）。"""
    session = _FakeSession()

    touched = await persist_sub_questions(
        _as_session(session), run_id="run-1", items=[_sq("sq-1"), _sq("sq-2")]
    )
    assert touched == 2
    assert len(session.added) == 2
    assert all(isinstance(o, SubQuestion) and o.run_id == "run-1" for o in session.added)

    # 状态推进 + 证据引用回填：同 ID 应被 upsert 而不是重复插入
    await persist_sub_questions(
        _as_session(session),
        run_id="run-1",
        items=[_sq("sq-1", status="succeeded", evidence_ids=["ev-1", "ev-2"]), _sq("sq-2")],
    )
    rows = list(session._subs.values())
    assert len(rows) == 2
    sq1 = session._subs["sq-1"]
    assert sq1.status == "succeeded"
    assert sq1.evidence_count == 2


@pytest.mark.asyncio
async def test_persist_sub_questions_skips_existing_identity() -> None:
    """已在库的行不重复 add（仅更新字段）。"""
    existing = SubQuestion(
        id="sq-exist",
        run_id="run-1",
        question="旧问题",
        depends_on=[],
        status="running",
        evidence_count=0,
    )
    session = _FakeSession(existing_subs=[existing])

    await persist_sub_questions(
        _as_session(session),
        run_id="run-1",
        items=[_sq("sq-exist", status="succeeded", evidence_ids=["ev-9"])],
    )
    assert session.added == []
    assert existing.status == "succeeded"
    assert existing.evidence_count == 1


@pytest.mark.asyncio
async def test_persist_evidence_is_idempotent_and_complete() -> None:
    """证据按 ID 幂等插入；重复执行不产生重复行，必填/分类字段齐备（TR-3.1/3.3）。"""
    session = _FakeSession()
    items = [_ev("ev-1"), _ev("ev-2", sub_question_id="sq-2")]

    added_first = await persist_evidence(_as_session(session), run_id="run-1", items=items)
    added_second = await persist_evidence(_as_session(session), run_id="run-1", items=items)
    assert [r.id for r in added_first] == ["ev-1", "ev-2"]
    assert added_second == []
    rows = [o for o in session.added if isinstance(o, Evidence)]
    assert len(rows) == 2

    by_id = {r.id: r for r in rows}
    ev1 = by_id["ev-1"]
    assert ev1.run_id == "run-1"
    assert ev1.sub_question_id == "sq-1"
    assert ev1.source_level == "secondary"
    assert ev1.credibility == "B"
    assert ev1.source_type == "news"
    assert ev1.relevance_score == 0.8
    assert ev1.fingerprint == "fp-ev-1"
    assert ev1.published_at is not None
    assert ev1.fetched_at is not None
    assert ev1.content is None


@pytest.mark.asyncio
async def test_persist_evidence_fills_optional_defaults() -> None:
    """缺省字段（relevance_score/published_at/分级枚举）按安全默认落库。"""
    session = _FakeSession()
    minimal = _ev("ev-x")
    minimal["relevance_score"] = None
    minimal["published_at"] = None
    minimal["source_level"] = ""
    minimal["credibility"] = ""
    minimal["source_type"] = ""

    await persist_evidence(_as_session(session), run_id="run-1", items=[minimal])
    row = next(o for o in session.added if isinstance(o, Evidence))
    assert row.relevance_score == 0.0
    assert row.published_at is None
    assert row.fetched_at is not None
    assert row.source_level == "tertiary"
    assert row.credibility == "D"
    assert row.source_type == "search"


@pytest.mark.asyncio
async def test_standardizer_node_persists_classified_evidence_once() -> None:
    """standardizer 注入会话后：分类证据与收敛后子问题落库；重放不重复（TR-3.2）。"""
    from app.orchestrator.dependencies import NodeDeps
    from app.orchestrator.nodes import standardizer

    session = _FakeSession()
    deps = NodeDeps(run_id="run-1", team_id="t1", trace_id="tr1", db_session=_as_session(session))
    state = {
        "run_id": "run-1",
        "sub_questions": [_sq("sq-1", status="succeeded", evidence_ids=["ev-1", "ev-dup"])],
        "evidence": [
            _ev("ev-1"),
            {**_ev("ev-dup"), "fingerprint": "fp-ev-1"},
        ],
    }

    result1 = await standardizer.run(state, deps=deps)
    result2 = await standardizer.run(
        {**state, "standardized_evidence": result1["standardized_evidence"]},
        deps=deps,
    )

    # 同指纹去重后仅 1 条分类证据，且只落 1 次
    evidence_rows = [o for o in session.added if isinstance(o, Evidence)]
    assert len(evidence_rows) == 1
    assert result1["standardized_evidence"][0]["id"] == "ev-1"
    # 第二跳：证据不重复插入；子问题引用已收敛为单证据
    assert len(result2["standardized_evidence"]) == 1
    sq_row = next(o for o in session.added if isinstance(o, SubQuestion))
    assert sq_row.evidence_count == 1


# ---------------------------------------------------------------------------
# persist_conflicts（M2-2 Task 5）
# ---------------------------------------------------------------------------


def _conflict(cid: str, *, severity: str = "high", status: str = "awaiting_human") -> dict[str, Any]:
    return {
        "id": cid,
        "claim": f"议题-{cid}",
        "evidence_a_id": f"{cid}-a",
        "evidence_b_id": f"{cid}-b",
        "type": "factual",
        "severity": severity,
        "status": status,
    }


@pytest.mark.asyncio
async def test_persist_conflicts_maps_status_and_resolved_at() -> None:
    """TR-5.1/5.2：resolved 写 resolved_at；awaiting_human 留空。"""
    session = _FakeSession()
    added = await persist_conflicts(
        _as_session(session),
        run_id="run-1",
        items=[_conflict("c1", severity="medium", status="resolved"), _conflict("c2")],
    )
    assert added == 2
    rows = [o for o in session.added if isinstance(o, Conflict)]
    by_id = {r.id: r for r in rows}
    assert by_id["c1"].status == "resolved"
    assert by_id["c1"].resolved_at is not None
    assert by_id["c2"].status == "awaiting_human"
    assert by_id["c2"].resolved_at is None
    assert all(r.run_id == "run-1" for r in rows)
    assert by_id["c1"].severity == "medium"


@pytest.mark.asyncio
async def test_persist_conflicts_skips_existing_id() -> None:
    """TR-5.5：同 ID 冲突恢复重放不重复插入。"""
    session = _FakeSession(existing_conflict_ids={"c1"})
    added = await persist_conflicts(
        _as_session(session),
        run_id="run-1",
        items=[_conflict("c1"), _conflict("c2")],
    )
    assert added == 1
    rows = [o for o in session.added if isinstance(o, Conflict)]
    assert [r.id for r in rows] == ["c2"]


@pytest.mark.asyncio
async def test_persist_conflicts_empty_is_noop() -> None:
    session = _FakeSession()
    assert await persist_conflicts(_as_session(session), run_id="run-1", items=[]) == 0
    assert session.added == []


# ---------------------------------------------------------------------------
# 阶段行（M2-4 T1）
# ---------------------------------------------------------------------------


def _stage(name: str, *, status: str = "pending") -> Stage:
    now = datetime.now(tz=UTC)
    return Stage(
        id=new_ulid(),
        run_id="run-1",
        name=name,
        status=status,
        attempt=1,
        token_used=0,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_ensure_stage_rows_inserts_six_then_idempotent() -> None:
    """AC-1：首次补齐六行 pending；再次调用取回既有行不新增、不重置。"""
    session = _FakeSession()

    rows = await ensure_stage_rows(_as_session(session), run_id="run-1")
    assert list(rows.keys()) == list(STAGE_ORDER)
    added = [o for o in session.added if isinstance(o, Stage)]
    assert len(added) == 6
    assert all(r.status == "pending" and r.attempt == 1 for r in added)

    # 第二次：假会话已持有六行，不再 add；返回映射同名同行
    session.added.clear()
    again = await ensure_stage_rows(_as_session(session), run_id="run-1")
    assert session.added == []
    assert again["clarify"] is rows["clarify"]


def test_apply_stage_transition_rules() -> None:
    """AC-1：running 写 started_at 一次；succeeded 收口不可回退；failed 带 error_code。"""
    row = _stage("clarify")
    started = datetime(2026, 1, 1, tzinfo=UTC)

    apply_stage_transition(row, status="running", now=started)
    assert row.status == "running"
    assert row.started_at == started

    # 再次迁移 running：started_at 保持首值
    apply_stage_transition(row, status="running", now=datetime(2026, 1, 2, tzinfo=UTC))
    assert row.started_at == started

    # 收口：finished_at/token_used
    apply_stage_transition(
        row,
        status="succeeded",
        token_used=320,
        finished=True,
        now=datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert row.finished_at is not None
    assert row.token_used == 320

    # 已 succeeded 不回退（恢复重放保护）
    apply_stage_transition(row, status="running", now=datetime(2026, 1, 4, tzinfo=UTC))
    assert row.status == "succeeded"
    apply_stage_transition(row, status="failed", error_code="X", finished=True)
    assert row.status == "succeeded"
    assert row.error_code is None


def test_apply_stage_transition_failed_writes_error_code() -> None:
    """失败终态：current_stage 行置 failed 并写 error_code/finished_at。"""
    row = _stage("critique", status="running")
    apply_stage_transition(row, status="running", now=datetime(2026, 1, 1, tzinfo=UTC))
    apply_stage_transition(
        row,
        status="failed",
        error_code="RuntimeError",
        finished=True,
        token_used=88,
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    assert row.status == "failed"
    assert row.error_code == "RuntimeError"
    assert row.finished_at is not None
    assert row.token_used == 88


@pytest.mark.asyncio
async def test_persist_stage_transition_inserts_missing_as_running() -> None:
    """缺行场景：upsert 直接插入 running 行并写 started_at（执行器外的兼容入口）。"""
    session = _FakeSession()
    row = await persist_stage_transition(
        _as_session(session), run_id="run-1", name="report", status="running"
    )
    assert row.name == "report"
    assert row.status == "running"
    assert row.started_at is not None
    assert session.added and session.added[-1] is row
