"""fan-out 超步边界消费主动介入的节点测试（M2-5 AC-8/AC-9）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.db.base import new_ulid
from app.db.models.intervention import RunIntervention
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes.researcher_fan_out import run as fan_out_run
from app.orchestrator.state import SubQuestionDict
from app.realtime.hub import RealtimeHub
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource

_RUN_ID = "run-iv-001"


class _FixedRetrieval:
    """每个查询按 query 文本返回预设 hit（未预设则返回单条固定证据）。"""

    def __init__(self, hits_by_query: dict[str, list[RetrievalHit]]) -> None:
        self._hits = hits_by_query
        self.search_calls = 0

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        self.search_calls += 1
        if request.query in self._hits:
            return list(self._hits[request.query])
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title=f"命中-{request.query}",
                url=f"https://example.com/{request.query}",
                snippet="摘要",
                score=0.8,
            )
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(hits)

    async def aclose(self) -> None:
        return None


class _BoundarySession:
    """fan-out 消费介入所需的最小假会话（sub_questions/run_interventions/evidence）。"""

    def __init__(self, interventions: list[RunIntervention], excluded: set[str]) -> None:
        self._interventions = interventions
        self._excluded = excluded
        self.subs: dict[str, Any] = {}
        self.applied: list[str] = []

    async def scalars(self, stmt: Any) -> Any:
        sql = str(stmt)

        class _Result:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def all(self) -> list[Any]:
                return self._rows

        if "FROM sub_questions" in sql:
            return _Result(list(self.subs.values()))
        if "FROM run_interventions" in sql:
            return _Result([iv for iv in self._interventions if iv.status == "pending"])
        if "FROM evidence" in sql:
            return _Result(list(self._excluded))
        raise AssertionError(f"未预期的查询: {sql}")

    def add(self, obj: Any) -> None:
        self.subs[obj.id] = obj

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        # mark_applied：WHERE id = :id 置 applied
        where = getattr(stmt, "whereclause", None)
        target_id = None
        for clause in getattr(where, "clauses", [where]):
            left = getattr(getattr(clause, "left", None), "name", None)
            if left == "id":
                target_id = getattr(clause.right, "value", None)
        for iv in self._interventions:
            if iv.id == target_id:
                iv.status = "applied"
                self.applied.append(iv.id)

        class _Cursor:
            rowcount = 1

        return _Cursor()


def _hit(ev_id: str, *, query: str) -> RetrievalHit:
    return RetrievalHit(
        source=RetrievalSource.WEB,
        title=f"证据-{ev_id}",
        url=f"https://example.com/{ev_id}",
        snippet=f"摘要-{query}",
        score=0.9,
    )


def _sq(sq_id: str, question: str) -> SubQuestionDict:
    return {
        "id": sq_id,
        "question": question,
        "depends_on": [],
        "status": "pending",
        "evidence_ids": [],
    }


def _state(subqs: list[SubQuestionDict], evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "run_id": _RUN_ID,
        "tier": "standard",
        "sub_questions": subqs,
        "evidence": evidence or [],
    }


def _deps(client: Any, session: Any, hub: RealtimeHub) -> NodeDeps:
    return NodeDeps(
        run_id=_RUN_ID,
        team_id="team-1",
        trace_id="tr-1",
        llm=None,
        retrieval_client=client,
        db_session=session,
        hub=hub,
    )


@pytest.mark.asyncio
async def test_fan_out_consumes_pending_followup_as_new_layer() -> None:
    """AC-8：层边界的 ask_followup 追加为新补查层，created→started→finished 帧齐全。"""
    intervention = RunIntervention(
        id=new_ulid(),
        run_id=_RUN_ID,
        user_id="user-1",
        type="ask_followup",
        payload={"sub_question_id": "sq-1", "question": "补查近三年"},
        status="pending",
    )
    session = _BoundarySession([intervention], excluded=set())
    hub = RealtimeHub()
    client = _FixedRetrieval({})
    deps = _deps(client, session, hub)

    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe(f"runs:{_RUN_ID}"):
            events.append(event)

    collect_task = asyncio.create_task(_collect())
    await asyncio.sleep(0)  # 等订阅器在 hub 注册后再执行
    try:
        patch = await fan_out_run(_state([_sq("sq-1", "原问题")]), deps=deps)
        # 给收集器排空末帧队列的调度窗口，再取消订阅
        await asyncio.sleep(0)
    finally:
        collect_task.cancel()
        await asyncio.gather(collect_task, return_exceptions=True)

    # 原始子问题 + followup 均执行成功，followup 依赖指向 sq-1
    subqs = patch["sub_questions"]
    assert len(subqs) == 2
    followup = next(s for s in subqs if s["id"] != "sq-1")
    assert followup["depends_on"] == ["sq-1"]
    assert followup["status"] == "succeeded"
    assert followup["question"] == "补查近三年"
    # 两轮检索各产出 1 条证据
    assert len(patch["evidence"]) == 2
    assert client.search_calls == 2
    # 介入行被标记 applied
    assert intervention.status == "applied"
    assert session.applied == [intervention.id]

    # 帧序列：sq-1 started/finished → created → followup started/finished
    types = [e["type"] for e in events]
    assert types == [  # noqa: S101
        "sub_question.started",
        "sub_question.finished",
        "sub_question.created",
        "sub_question.started",
        "sub_question.finished",
    ], types
    created = events[2]
    assert created["payload"]["sub_question_id"] == followup["id"]
    assert created["payload"]["depends_on"] == ["sq-1"]
    assert events[3]["payload"]["sub_question_id"] == followup["id"]


@pytest.mark.asyncio
async def test_fan_out_excludes_marked_evidence_from_state() -> None:
    """AC-9：DB 中 excluded 的证据在边界从 state 证据集与子问题引用中剔除。"""
    session = _BoundarySession([], excluded={"ev-1"})
    hub = RealtimeHub()
    client = _FixedRetrieval({})
    deps = _deps(client, session, hub)

    patch = await fan_out_run(
        _state(
            [_sq("sq-1", "问题一"), _sq("sq-2", "问题二")],
            evidence=[{"id": "ev-old", "sub_question_id": "sq-1", "evidence_ids": ["ev-old"]}],
        ),
        deps=deps,
    )
    evidence_ids = {e["id"] for e in patch["evidence"]}
    assert "ev-1" not in evidence_ids
    for sq in patch["sub_questions"]:
        assert "ev-1" not in sq.get("evidence_ids", [])
