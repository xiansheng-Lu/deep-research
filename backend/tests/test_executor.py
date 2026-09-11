"""``executor`` 模块单元测试。

覆盖：
- ``_build_initial_state``：初始状态字段正确
- ``_compile_for_deps``：图可编译
- ``run_research_async``：成功路径标记 succeeded + 新建 Report
- ``run_research_async``：run 不存在时不崩
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.run import ResearchRun
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.executor import (
    _build_initial_state,
    _compile_for_deps,
    run_research_async,
)
from app.orchestrator.state import ResearchStage
from app.realtime.hub import RealtimeHub
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient


def test_build_initial_state_has_required_fields() -> None:
    """初始状态必须包含 run_id / question / tier / token_budget / current_stage。"""
    state = _build_initial_state(
        run_id="test-run-001",
        project_id="test-proj-001",
        question="测试问题",
        template_id="generic",
        tier="standard",
        token_budget=150_000,
        clarification=None,
        trace_id="trace-001",
    )
    assert state["run_id"] == "test-run-001"
    assert state["project_id"] == "test-proj-001"
    assert state["question"] == "测试问题"
    assert state["tier"] == "standard"
    assert state["token_budget"] == 150_000
    assert state["token_used"] == 0
    assert state["current_stage"] == ResearchStage.CLARIFY
    assert state["started_at"] is not None
    assert state["finished_at"] is None


def test_compile_for_deps_produces_callable_graph() -> None:
    """_compile_for_deps 应返回可调用的编译图。"""
    deps = NodeDeps(
        run_id="test-run-001",
        team_id="test-team-001",
        trace_id="trace-001",
    )
    graph = _compile_for_deps(deps)
    assert graph is not None
    assert hasattr(graph, "ainvoke")


class _OfflineRetrievalClient(RetrievalClient):
    """返回固定命中的离线检索客户端，避免测试依赖真实网络。"""

    def __init__(self) -> None:
        # 跳过父类 __init__：不要求 primary/backup provider
        pass

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:  # noqa: ARG002
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="测试信源标题",
                url="https://example.com/test",
                snippet="测试检索摘要内容",
                score=0.9,
            )
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(hits)


@pytest.mark.asyncio
async def test_compiled_graph_runs_all_nodes_without_signature_error() -> None:
    """真实编译图可一路执行到 report（回归：纯 state 节点不得被注入 deps）。

    修复前 executor 给 failure_recovery 等纯 state 节点也闭包注入 deps，
    首个节点即抛 ``TypeError: run() got an unexpected keyword argument 'deps'``。
    """
    deps = NodeDeps(
        run_id="test-run-graph",
        team_id="test-team-001",
        trace_id="trace-001",
        llm=None,  # 走各节点降级路径，无 LLM 网络调用
        retrieval_client=_OfflineRetrievalClient(),
        db_session=None,
    )
    graph = _compile_for_deps(deps)
    initial_state = _build_initial_state(
        run_id="test-run-graph",
        project_id="proj-001",
        question="2026 年中国光伏装机量趋势如何？",
        template_id="generic",
        tier="standard",
        token_budget=100_000,
        clarification=None,
        trace_id="trace-001",
    )

    final_state = await graph.ainvoke(
        initial_state, config={"configurable": {"thread_id": "test-run-graph"}}
    )

    assert final_state["current_stage"] == ResearchStage.REPORT
    assert (final_state.get("report_draft") or "").strip()


@pytest.mark.asyncio
async def test_run_research_async_run_not_found_publishes_failed() -> None:
    """run 不存在时应发布 run.failed 事件并安全返回。"""
    hub = RealtimeHub()

    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe("runs:nonexistent-run"):
            events.append(event)
            break

    task = asyncio.create_task(_collect())

    # 假 session_factory 返回 scalar 返回 None 的 session
    mock_session = AsyncMock()
    mock_session.scalar.return_value = None
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    await run_research_async(
        run_id="nonexistent-run",
        project_id="proj-001",
        template_id="generic",
        tier="standard",
        question="测试问题",
        token_budget=150_000,
        clarification=None,
        team_id="team-001",
        creator_id="user-001",
        trace_id="trace-001",
        session_factory=mock_factory,
        llm=None,
        retrieval_client=None,
        hub=hub,
    )

    await asyncio.sleep(0.05)
    task.cancel()

    assert len(events) == 1
    assert events[0]["type"] == "run.failed"
    assert events[0]["error_code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_research_async_success_marks_succeeded() -> None:
    """成功路径：graph 返回带 report_draft 的 state → run 标记 succeeded + 新建 Report。"""
    fake_final_state: dict[str, Any] = {
        "run_id": "test-run-success",
        "current_stage": ResearchStage.REPORT,
        "token_used": 500,
        "report_draft": "## 研究背景\n\n测试报告。",
        "report_claims": [],
        "conflicts": [],
        "report_outline": [],
    }

    fake_graph = AsyncMock()
    fake_graph.ainvoke = AsyncMock(return_value=fake_final_state)

    run = ResearchRun(
        id="test-run-success",
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="standard",
        question="测试问题",
        status="pending",
        token_budget=150_000,
    )

    mock_session = AsyncMock()
    mock_session.scalar.return_value = run
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "app.orchestrator.executor._compile_for_deps",
            lambda deps: fake_graph,
        )
        await run_research_async(
            run_id="test-run-success",
            project_id="proj-001",
            template_id="generic",
            tier="standard",
            question="测试问题",
            token_budget=150_000,
            clarification=None,
            team_id="team-001",
            creator_id="user-001",
            trace_id="trace-001",
            session_factory=mock_factory,
            llm=None,
            retrieval_client=None,
            hub=hub,
        )

    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert run.token_used == 500
    assert mock_session.add.called
    added_obj = mock_session.add.call_args[0][0]
    assert "## 研究背景" in added_obj.content_md
    assert mock_session.commit.called
