"""M2-3/M2-4 实时增量帧的离线节点测试（AC-8/AC-9/AC-13 的节点侧部分）。

用收集型 hub 与 tests.test_persistence 的内存假会话，验证帧只在落库后发：
- sub_question.created（decompose，逐条、pending）；
- sub_question.started/finished（retrieve，含 succeeded/failed/evidence_short 终态）；
- evidence.fetched（standardize，仅新增证据、载荷无 content）。

阶段帧（stage.started/finished/failed）由执行器驱动，见执行器测试与真库集成。
"""

from __future__ import annotations

from typing import Any

import pytest
from test_persistence import _as_session, _FakeSession

from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes import researcher_fan_out, standardizer, sub_questioner
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient


class _CollectHub:
    """记录 (channel, event) 的 hub 替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        self.calls.append((channel, event))

    @property
    def events(self) -> list[dict[str, Any]]:
        return [event for _channel, event in self.calls]

    def types(self) -> list[str]:
        return [str(e["type"]) for e in self.events]


class _StubRetrieval(RetrievalClient):
    """检索客户端桩：search 返回可配置结果或抛错；extract 原样返回。"""

    def __init__(self, *, hits: list[RetrievalHit] | None = None, boom: bool = False) -> None:
        self._hits = hits
        self._boom = boom

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        if self._boom:
            raise RuntimeError("检索提供方故障")
        return list(self._hits or [])

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return hits


def _hit() -> RetrievalHit:
    return RetrievalHit(
        source=RetrievalSource.WEB,
        title="统计局权威页面",
        url="https://stats.gov.cn/policy/2026/index.html",
        snippet="政策摘要片段",
        score=0.9,
        content="正文内容（不应进入增量帧）",
        published_at=None,
        fetched_at=None,
    )


@pytest.mark.asyncio
async def test_sub_question_created_frames_after_persist() -> None:
    """AC-8：decompose 落库后逐条发 sub_question.created（quick 降级单子问题）。"""
    hub = _CollectHub()
    session = _FakeSession()
    deps = NodeDeps(
        run_id="run-1",
        team_id="team-1",
        trace_id="tr-1",
        db_session=_as_session(session),
        hub=hub,
    )

    await sub_questioner.run(
        {"run_id": "run-1", "tier": "quick", "question": "原始研究问题"},
        deps=deps,
    )

    events = hub.events
    assert hub.types() == ["sub_question.created"]
    frame = events[0]
    assert frame["stage"] == "decompose"
    payload = frame["payload"]
    assert payload["status"] == "pending"
    assert payload["depends_on"] == []
    assert payload["sub_question_id"]
    assert payload["question"]
    assert hub.calls[0][0] == "runs:run-1"
    # 先落库后发帧：会话内确有该子问题行
    assert any(row.id == payload["sub_question_id"] for row in session._subs.values())


@pytest.mark.asyncio
async def test_researcher_emits_started_and_finished_succeeded() -> None:
    """AC-8：每层 started（running）→ 落库 → finished（succeeded, evidence_count）。"""
    hub = _CollectHub()
    session = _FakeSession()
    deps = NodeDeps(
        run_id="run-1",
        team_id="team-1",
        trace_id="tr-1",
        retrieval_client=_StubRetrieval(hits=[_hit()]),
        db_session=_as_session(session),
        hub=hub,
    )
    state = {
        "run_id": "run-1",
        "tier": "quick",
        "sub_questions": [{"id": "sq-1", "question": "子问题1", "depends_on": [], "status": "pending"}],
    }

    patch = await researcher_fan_out.run(state, deps=deps)

    assert hub.types() == ["sub_question.started", "sub_question.finished"]
    started, finished = hub.events
    assert started["stage"] == "retrieve"
    assert started["payload"] == {"sub_question_id": "sq-1", "status": "running"}
    assert finished["payload"]["status"] == "succeeded"
    assert finished["payload"]["evidence_count"] == 1
    assert len(patch["evidence"]) == 1
    assert patch["sub_questions"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_researcher_finished_frame_reflects_terminal_status() -> None:
    """AC-8：检索空结果 → evidence_short；检索抛错 → failed；终态如实上帧。"""
    # 空结果：evidence_short
    hub_short = _CollectHub()
    deps_short = NodeDeps(
        run_id="r",
        team_id="t",
        trace_id="x",
        retrieval_client=_StubRetrieval(hits=[]),
        hub=hub_short,
    )
    state = {
        "run_id": "r",
        "tier": "quick",
        "sub_questions": [{"id": "sq", "question": "q", "depends_on": [], "status": "pending"}],
    }
    await researcher_fan_out.run(state, deps=deps_short)
    finished_short = hub_short.events[-1]["payload"]
    assert finished_short["status"] == "evidence_short"
    assert finished_short["evidence_count"] == 0

    # 抛错：failed
    hub_fail = _CollectHub()
    session = _FakeSession()
    deps_fail = NodeDeps(
        run_id="r",
        team_id="t",
        trace_id="x",
        retrieval_client=_StubRetrieval(boom=True),
        db_session=_as_session(session),
        hub=hub_fail,
    )
    await researcher_fan_out.run(state, deps=deps_fail)
    assert hub_fail.types() == ["sub_question.started", "sub_question.finished"]
    assert hub_fail.events[-1]["payload"]["status"] == "failed"
    # 失败终态同样落库
    assert session._subs["sq"].status == "failed"


@pytest.mark.asyncio
async def test_standardizer_emits_evidence_fetched_only_for_new_rows() -> None:
    """AC-9：evidence.fetched 仅对本次新增证据逐条发；重放不重复；载荷无 content。"""
    hub = _CollectHub()
    session = _FakeSession()
    deps = NodeDeps(
        run_id="run-1",
        team_id="team-1",
        trace_id="tr-1",
        db_session=_as_session(session),
        hub=hub,
    )
    state = {
        "run_id": "run-1",
        "tier": "quick",
        "sub_questions": [{"id": "sq-1", "question": "子问题1", "depends_on": [], "status": "succeeded"}],
        "evidence": [
            {
                "id": "ev-1",
                "sub_question_id": "sq-1",
                "url": "https://stats.gov.cn/policy/2026/index.html",
                "domain": "stats.gov.cn",
                "title": "统计局权威页面",
                "snippet": "政策摘要片段",
                "source_type": "official_doc",
                "source_level": "tertiary",
                "credibility": "C",
                "relevance_score": 0.9,
                "fingerprint": "fp-1",
                "published_at": None,
                "fetched_at": "2026-09-02T00:00:00+00:00",
            }
        ],
    }

    result1 = await standardizer.run(state, deps=deps)
    result2 = await standardizer.run(
        {**state, "standardized_evidence": result1["standardized_evidence"]},
        deps=deps,
    )

    fetched = [e for e in hub.events if e["type"] == "evidence.fetched"]
    assert len(fetched) == 1  # 第二跳重放不重复推送
    frame = fetched[0]
    assert frame["stage"] == "standardize"
    payload = frame["payload"]
    assert payload["id"] == result1["standardized_evidence"][0]["id"]
    assert payload["sub_question_id"] == "sq-1"
    assert payload["domain"] == "stats.gov.cn"
    assert payload["source_level"] == "primary"
    assert payload["credibility"] in {"A", "B", "C", "D"}
    assert payload["excluded_by_user"] is False
    assert "content" not in payload
    # 第二跳没有产生任何新事件
    assert len(hub.events) == 1
    assert result2["standardized_evidence"]
