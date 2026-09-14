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
from langgraph.checkpoint.memory import InMemorySaver

from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.executor import (
    _build_initial_state,
    _compile_for_deps,
    resume_research_async,
    run_research_async,
)
from app.orchestrator.registry import get_run_registry
from app.orchestrator.schemas import ClarificationSchema, SubQuestionListSchema
from app.orchestrator.state import ResearchStage
from app.provider.client import LLMClient, StructuredCompletion
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

    final_state = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": "test-run-graph"}})

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


class _FakeAsyncGraphStream:
    """模拟 LangGraph 编译图的 ``astream``（values 模式：逐 super-step 产出完整 state）。"""

    def __init__(self, snapshots: list[dict[str, Any]]) -> None:
        self._snapshots = snapshots

    def astream(self, initial_state: Any, config: Any = None, **kwargs: Any) -> Any:  # noqa: ARG002
        # 生产侧固定以 stream_mode="values" 调用，mock 仅校验契约不消费该参数
        assert kwargs.get("stream_mode") == "values"
        snapshots = self._snapshots

        class _Iterator:
            def __init__(self) -> None:
                self._idx = 0

            def __aiter__(self) -> _Iterator:
                return self

            async def __anext__(self) -> dict[str, Any]:
                if self._idx >= len(snapshots):
                    raise StopAsyncIteration
                snap = snapshots[self._idx]
                self._idx += 1
                return snap

        return _Iterator()


@pytest.mark.asyncio
async def test_run_research_async_success_marks_succeeded() -> None:
    """成功路径：astream 产出终态 report_draft → succeeded + 新建 Report + 阶段事件。"""
    # 模拟六个 super-step 后的 state 快照（values 模式）
    stage_sequence = [
        ResearchStage.CLARIFY,
        ResearchStage.DECOMPOSE,
        ResearchStage.RETRIEVE,
        ResearchStage.STANDARDIZE,
        ResearchStage.CRITIQUE,
        ResearchStage.REPORT,
    ]
    snapshots: list[dict[str, Any]] = []
    for idx, stage in enumerate(stage_sequence, start=1):
        snapshots.append(
            {
                "run_id": "test-run-success",
                "current_stage": stage,
                "token_used": 100 * idx,
                "stage_attempts": {},
                "report_draft": "",
                "report_claims": [],
                "conflicts": [],
                "report_outline": [],
            }
        )
    snapshots[-1]["report_draft"] = "## 研究背景\n\n测试报告。"
    snapshots[-1]["token_used"] = 500

    fake_graph = _FakeAsyncGraphStream(snapshots)

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
    # 过程数据持久化查询（子问题/证据幂等检查）在本测试无既有行；
    # AsyncMock 的子属性仍为 AsyncMock，直接 await 后 .all() 会返回协程，
    # 因此 scalars 的结果对象显式用 MagicMock 构造
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    mock_session.scalars = AsyncMock(return_value=_scalars_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()

    # 订阅事件：收集到终态 run.finished 为止
    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe("runs:test-run-success"):
            events.append(event)
            if event.get("type") == "run.finished":
                break

    collect_task = asyncio.create_task(_collect())

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "app.orchestrator.executor._compile_for_deps",
            lambda deps, checkpointer=None: fake_graph,
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

    await asyncio.sleep(0.05)
    collect_task.cancel()

    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert run.token_used == 500
    assert mock_session.add.called
    added_obj = mock_session.add.call_args[0][0]
    assert "## 研究背景" in added_obj.content_md
    assert mock_session.commit.called

    # 六个阶段切换各推一条 stage.started，末尾一条 run.finished
    stage_events = [e for e in events if e["type"] == "stage.started"]
    assert [e["stage"] for e in stage_events] == [s.value for s in stage_sequence]
    assert stage_events[0]["payload"]["stage"] == "clarify"
    assert stage_events[-1]["payload"]["token_used"] == 500

    # AC-13：阶段收口帧——切换时前五阶段各一条 succeeded，终态再补发 report
    stage_finished = [e for e in events if e["type"] == "stage.finished"]
    assert [e["stage"] for e in stage_finished] == [s.value for s in stage_sequence]
    assert all(e["payload"]["status"] == "succeeded" for e in stage_finished)
    assert all(e["payload"]["attempt"] == 1 for e in stage_finished)
    assert all(isinstance(e["payload"]["duration_ms"], int) for e in stage_finished)
    assert stage_finished[-1]["payload"]["token_used"] == 500
    # 成功路径不发 stage.failed
    assert not [e for e in events if e["type"] == "stage.failed"]
    # 收尾顺序：report 收口 → 强制 token 帧 → run.finished
    assert events[-3]["type"] == "stage.finished"
    assert events[-3]["stage"] == "report"
    assert events[-2]["type"] == "token.usage.update"
    finished_events = [e for e in events if e["type"] == "run.finished"]
    assert len(finished_events) == 1
    assert finished_events[0]["status"] == "succeeded"


class _BoomAfterFirstGraphStream:
    """首份快照（clarify）后抛错的假图，覆盖异常终态路径。"""

    def astream(self, initial_state: Any, config: Any = None, **kwargs: Any) -> Any:  # noqa: ARG002
        assert kwargs.get("stream_mode") == "values"

        class _Iterator:
            def __init__(self) -> None:
                self._done = False

            def __aiter__(self) -> _Iterator:
                return self

            async def __anext__(self) -> dict[str, Any]:
                if self._done:
                    raise StopAsyncIteration
                self._done = True
                return {
                    "run_id": "test-run-graph-error",
                    "current_stage": ResearchStage.CLARIFY,
                    "token_used": 50,
                    "stage_attempts": {},
                    "report_draft": "",
                    "report_claims": [],
                    "conflicts": [],
                    "report_outline": [],
                }

        return _Iterator()


@pytest.mark.asyncio
async def test_run_research_async_graph_raises_publishes_stage_failed() -> None:
    """AC-13：图执行异常 → 当前阶段行 failed + stage.failed + run.finished(failed)。"""

    class _RaisingStream(_BoomAfterFirstGraphStream):
        def astream(self, initial_state: Any, config: Any = None, **kwargs: Any) -> Any:
            assert kwargs.get("stream_mode") == "values"
            inner = super().astream(initial_state, config, **kwargs)

            class _Iterator:
                def __aiter__(self) -> Any:
                    return self

                async def __anext__(self) -> dict[str, Any]:
                    await inner.__anext__()  # 消费首份 clarify 快照后立即炸
                    raise RuntimeError("图执行炸了")

            return _Iterator()

    run = ResearchRun(
        id="test-run-graph-error",
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
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    mock_session.scalars = AsyncMock(return_value=_scalars_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()
    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe("runs:test-run-graph-error"):
            events.append(event)
            if event.get("type") == "run.finished":
                break

    collect_task = asyncio.create_task(_collect())

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "app.orchestrator.executor._compile_for_deps",
            lambda deps, checkpointer=None: _RaisingStream(),
        )
        await run_research_async(
            run_id="test-run-graph-error",
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
    collect_task.cancel()

    assert run.status == "failed"
    assert run.error_code == "RuntimeError"
    # 当前阶段行（clarify）已置 failed 并记录错误码
    stage_rows = [c.args[0] for c in mock_session.add.call_args_list]
    clarify = next(r for r in stage_rows if getattr(r, "name", None) == "clarify")
    assert clarify.status == "failed"
    assert clarify.error_code == "RuntimeError"

    stage_failed = [e for e in events if e["type"] == "stage.failed"]
    assert len(stage_failed) == 1
    payload = stage_failed[0]["payload"]
    assert payload == {
        "stage": "clarify",
        "attempt": 1,
        "status": "failed",
        "error_code": "RuntimeError",
        "error_message": "RuntimeError('图执行炸了')",
        "retryable": False,
    }
    finished = [e for e in events if e["type"] == "run.finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "failed"
    assert finished[0]["error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_run_research_async_real_graph_values_stream() -> None:
    """真实编译图（不 mock graph）端到端回归。

    锁定两个真实链路契约：
    1. astream 必须显式 stream_mode="values"（默认 updates 会导致取不到
       current_stage / report_draft，终态被误判 paused）；
    2. values 快照中 current_stage 以 ResearchStage(StrEnum) 形式存在，
       事件载荷必须归一化为纯字符串并按 6 阶段顺序各推一条。
    """
    run = ResearchRun(
        id="test-run-real-graph",
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="quick",
        question="2026 年中国光伏装机量趋势如何？请给出有证据的分析。",
        status="pending",
        token_budget=50_000,
    )

    mock_session = AsyncMock()
    mock_session.scalar.return_value = run
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    # 过程数据持久化查询（子问题/证据幂等检查）在本测试无既有行；
    # AsyncMock 的子属性仍为 AsyncMock，直接 await 后 .all() 会返回协程，
    # 因此 scalars 的结果对象显式用 MagicMock 构造
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    mock_session.scalars = AsyncMock(return_value=_scalars_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()
    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe("runs:test-run-real-graph"):
            events.append(event)
            if event.get("type") == "run.finished":
                break

    collect_task = asyncio.create_task(_collect())

    await run_research_async(
        run_id="test-run-real-graph",
        project_id="proj-001",
        template_id="generic",
        tier="quick",
        question="2026 年中国光伏装机量趋势如何？请给出有证据的分析。",
        token_budget=50_000,
        clarification=None,
        team_id="team-001",
        creator_id="user-001",
        trace_id="trace-001",
        session_factory=mock_factory,
        llm=None,
        retrieval_client=_OfflineRetrievalClient(),
        hub=hub,
    )

    await asyncio.sleep(0.05)
    collect_task.cancel()

    assert run.status == "succeeded"
    assert run.current_stage == "report"
    added_report = mock_session.add.call_args[0][0]
    assert len(added_report.content_md) > 200

    stage_events = [e for e in events if e["type"] == "stage.started"]
    assert [e["stage"] for e in stage_events] == [
        "clarify",
        "decompose",
        "retrieve",
        "standardize",
        "critique",
        "report",
    ]
    # StrEnum 不得泄漏到对外载荷
    assert all(isinstance(e["stage"], str) and type(e["stage"]) is str for e in stage_events)
    finished_events = [e for e in events if e["type"] == "run.finished"]
    assert len(finished_events) == 1
    assert finished_events[0]["status"] == "succeeded"


class _BudgetedStructuredLLM(LLMClient):
    """按 schema 返回有效结构化结果，并为每次调用上报固定 token 用量。

    跳过父类 ``__init__`` 避免熔断器/注册表副作用；仅服务于预算闸门测试。
    """

    def __init__(self, tokens_per_call: int = 60) -> None:
        self.tokens_per_call = tokens_per_call
        self.call_tags: list[str] = []

    async def complete_structured(  # type: ignore[override]
        self,
        *,
        messages: list[Any],
        schema: type,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
    ) -> StructuredCompletion:
        self.call_tags.append(",".join(tags or []))
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": self.tokens_per_call,
        }
        if schema is ClarificationSchema:
            return StructuredCompletion(
                parsed=ClarificationSchema(
                    requires_user_input=False,
                    questions=[],
                    defaults={},
                    structured_question={
                        "goal": "预算闸门验证目标",
                        "scope": "测试范围",
                        "key_concepts": [],
                    },
                ),
                usage=usage,
                model="stub-budget-llm",
                raw="",
            )
        return StructuredCompletion(
            parsed=SubQuestionListSchema(
                sub_questions=[
                    {
                        "question": "预算闸门验证子问题",
                        "depends_on": [],
                        "rationale": "预算闸门测试",
                    }
                ]
            ),
            usage=usage,
            model="stub-budget-llm",
            raw="",
        )


@pytest.mark.asyncio
async def test_run_research_async_budget_exceeded_pauses_before_report() -> None:
    """SDP M1 验收准则 4：token 超预算 90% 时自动停止，不超支。

    clarify/decompose 各上报 60 token（累计 120 > 100×90%=90），
    cost_checkpoint 条件边路由 user_intervention，图在 interrupt_before
    挂起点停止。断言：run 标 paused、不新建 Report、阶段事件止于审视、
    终态事件为 paused 且 token 用量如实回写。
    """
    run = ResearchRun(
        id="test-run-budget",
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="quick",
        question="预算闸门自动停止验证问题",
        status="pending",
        token_budget=100,
    )

    mock_session = AsyncMock()
    mock_session.scalar.return_value = run
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    # 过程数据持久化查询（子问题/证据幂等检查）在本测试无既有行；
    # AsyncMock 的子属性仍为 AsyncMock，直接 await 后 .all() 会返回协程，
    # 因此 scalars 的结果对象显式用 MagicMock 构造
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    mock_session.scalars = AsyncMock(return_value=_scalars_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()
    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe("runs:test-run-budget"):
            events.append(event)
            if event.get("type") == "run.finished":
                break

    collect_task = asyncio.create_task(_collect())

    await run_research_async(
        run_id="test-run-budget",
        project_id="proj-001",
        template_id="generic",
        tier="quick",
        question="预算闸门自动停止验证问题",
        token_budget=100,
        clarification=None,
        team_id="team-001",
        creator_id="user-001",
        trace_id="trace-001",
        session_factory=mock_factory,
        llm=_BudgetedStructuredLLM(tokens_per_call=60),
        retrieval_client=_OfflineRetrievalClient(),
        hub=hub,
    )

    await asyncio.sleep(0.05)
    collect_task.cancel()

    # 挂起而非成功/失败：等待用户介入，finished_at 不得写入
    assert run.status == "paused"
    assert run.finished_at is None
    # 两次结构化调用的真实 token 用量如实回写（120），但执行在报告前已停止
    assert run.token_used == 120
    # 不超支的直接证据：挂起路径允许证据落库，但绝不产出 Report
    added_objs = [call.args[0] for call in mock_session.add.call_args_list]
    assert not any(isinstance(obj, Report) for obj in added_objs)
    assert mock_session.commit.called

    # 阶段事件止于审视：成本闸门不产生新阶段，report 绝不执行
    stage_events = [e for e in events if e["type"] == "stage.started"]
    assert [e["stage"] for e in stage_events] == [
        "clarify",
        "decompose",
        "retrieve",
        "standardize",
        "critique",
    ]
    # paused 语义：已切过的四阶段正常收口；当前 critique 保持 running 不发终态帧
    stage_finished = [e for e in events if e["type"] == "stage.finished"]
    assert [e["stage"] for e in stage_finished] == [
        "clarify",
        "decompose",
        "retrieve",
        "standardize",
    ]
    assert not [e for e in events if e["type"] == "stage.failed"]
    finished_events = [e for e in events if e["type"] == "run.finished"]
    assert len(finished_events) == 1
    assert finished_events[0]["status"] == "paused"
    assert finished_events[0]["token_used"] == 120


@pytest.mark.asyncio
async def test_resume_research_async_from_gate_completes_with_shared_saver() -> None:
    """HITL 恢复（AC-9）：共享持久检查点下，新编译图实例可从挂起线程续跑。

    首次执行因预算超 90% 在 ``user_intervention`` 的 interrupt_before 挂起；
    用同一个 InMemorySaver（等价跨请求/跨实例存活的持久 saver）经
    ``resume_research_async`` 注入 ``kind=proceed`` 续跑，图从挂起点继续执行到
    report 并成功收尾。
    """
    run_id = "test-run-resume"
    run = ResearchRun(
        id=run_id,
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="quick",
        question="HITL 恢复验证问题",
        status="pending",
        token_budget=100,
    )

    mock_session = AsyncMock()
    # scalar 三次调用：首次执行加载 run；恢复时再加载 run；随后查 project.team_id
    mock_session.scalar.side_effect = [run, run, "team-001"]
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    # 过程数据持久化查询（子问题/证据幂等检查）在本测试无既有行；
    # AsyncMock 的子属性仍为 AsyncMock，直接 await 后 .all() 会返回协程，
    # 因此 scalars 的结果对象显式用 MagicMock 构造
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    mock_session.scalars = AsyncMock(return_value=_scalars_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()

    async def _collect_one() -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        async for event in hub.subscribe(f"runs:{run_id}"):
            collected.append(event)
            if event.get("type") == "run.finished":
                break
        return collected

    shared_saver = InMemorySaver()

    collect_pause = asyncio.create_task(_collect_one())
    await run_research_async(
        run_id=run_id,
        project_id="proj-001",
        template_id="generic",
        tier="quick",
        question="HITL 恢复验证问题",
        token_budget=100,
        clarification=None,
        team_id="team-001",
        creator_id="user-001",
        trace_id="trace-resume",
        session_factory=mock_factory,
        llm=_BudgetedStructuredLLM(tokens_per_call=60),
        retrieval_client=_OfflineRetrievalClient(),
        hub=hub,
        checkpointer=shared_saver,
    )
    paused_events = await collect_pause
    assert run.status == "paused"
    assert paused_events[-1]["status"] == "paused"

    # 挂起线程已写入共享检查点：跨请求恢复的前提事实
    checkpoint_tuple = await shared_saver.aget_tuple({"configurable": {"thread_id": run_id}})
    assert checkpoint_tuple is not None

    # 模拟新请求：恢复入口内重新编译图，仅凭 thread_id 从检查点续跑
    collect_resume = asyncio.create_task(_collect_one())
    await asyncio.sleep(0)
    await resume_research_async(
        run_id=run_id,
        human_input={"kind": "proceed"},
        session_factory=mock_factory,
        checkpointer=shared_saver,
        llm=None,
        retrieval_client=_OfflineRetrievalClient(),
        hub=hub,
    )
    resumed_events = await collect_resume

    assert run.status == "succeeded"
    assert run.current_stage == "report"
    added_objs = [call.args[0] for call in mock_session.add.call_args_list]
    assert any(isinstance(obj, Report) for obj in added_objs)
    assert resumed_events[-1]["type"] == "run.finished"
    assert resumed_events[-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_resume_research_async_ignores_non_paused_run() -> None:
    """非 paused 状态的恢复请求被幂等忽略：不续跑、不推事件。"""
    run = ResearchRun(
        id="test-run-resume-ignore",
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="quick",
        question="不应恢复的问题",
        status="succeeded",
        token_budget=100,
        current_stage="report",
    )
    mock_session = AsyncMock()
    mock_session.scalar.return_value = run
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    hub = RealtimeHub()

    await resume_research_async(
        run_id="test-run-resume-ignore",
        human_input={"kind": "proceed"},
        session_factory=mock_factory,
        checkpointer=InMemorySaver(),
        llm=None,
        retrieval_client=None,
        hub=hub,
    )

    assert run.status == "succeeded"
    assert mock_session.commit.call_count == 0


# ---------------------------------------------------------------------------
# M2-5：协作式暂停/取消（RunRegistry + CancelledError 受控收尾）
# ---------------------------------------------------------------------------


class _GatedAsyncGraphStream:
    """产出给定快照后在 gate 上挂起的假图（取消信号在 gate await 点生效）。"""

    def __init__(self, snapshots: list[dict[str, Any]], gate: asyncio.Event) -> None:
        self._snapshots = snapshots
        self._gate = gate

    def astream(self, initial_state: Any, config: Any = None, **kwargs: Any) -> Any:  # noqa: ARG002
        assert kwargs.get("stream_mode") == "values"
        snapshots = self._snapshots
        gate = self._gate

        class _Iterator:
            def __init__(self) -> None:
                self._idx = 0

            def __aiter__(self) -> _Iterator:
                return self

            async def __anext__(self) -> dict[str, Any]:
                if self._idx < len(snapshots):
                    snap = snapshots[self._idx]
                    self._idx += 1
                    return snap
                # 模拟执行中的长 IO 节点：gate 不 set，只等取消信号打断
                await gate.wait()
                raise StopAsyncIteration

        return _Iterator()


def _build_control_mocks(run: ResearchRun, finalize_status: str) -> tuple[AsyncMock, MagicMock, list[Any]]:
    """构造执行会话与收尾会话两个 mock（模拟独立 DB 连接的不同视角）。

    - 执行会话：_load_run 的 scalar 返回 run（执行器随后置 running）；
    - 收尾会话：session.get 重读时模拟 DB 已被控制端点置为 finalize_status，
      查既有 Report 的 scalar 返回 None，条件 UPDATE 统一返回 rowcount=0。
    """
    added: list[Any] = []

    exec_session = AsyncMock()
    exec_session.scalar.return_value = run
    exec_session.add = MagicMock(side_effect=added.append)
    exec_session.flush = AsyncMock()
    exec_session.commit = AsyncMock()
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    exec_session.scalars = AsyncMock(return_value=_scalars_result)

    finalize_session = AsyncMock()

    async def _get(_model: Any, _pk: Any) -> ResearchRun:
        # 模拟独立连接读到控制端点已提交的状态
        run.status = finalize_status
        return run

    async def _scalar(_statement: Any) -> None:
        # 收尾会话仅查 select(Report.id)，无既有报告
        return None

    finalize_session.get = AsyncMock(side_effect=_get)
    finalize_session.scalar = AsyncMock(side_effect=_scalar)
    finalize_session.add = MagicMock(side_effect=added.append)
    finalize_session.flush = AsyncMock()
    finalize_session.commit = AsyncMock()
    finalize_session.refresh = AsyncMock()
    finalize_session.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(side_effect=[exec_session, finalize_session])
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return exec_session, mock_factory, added


async def _collect_until_finished(
    hub: RealtimeHub, run_id: str
) -> tuple[list[dict[str, Any]], asyncio.Task[None]]:
    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe(f"runs:{run_id}"):
            events.append(event)
            if event.get("type") == "run.finished":
                break

    return events, asyncio.create_task(_collect())


async def _drain_collector(collect_task: asyncio.Task[None]) -> None:
    """等收集器消费完 run.finished 自然退出，避免协程跨事件循环泄漏。"""
    await asyncio.wait_for(collect_task, timeout=2.0)


def _run_kwargs(
    run_id: str,
    mock_factory: MagicMock,
    hub: RealtimeHub,
) -> dict[str, Any]:
    return dict(
        run_id=run_id,
        project_id="proj-001",
        template_id="generic",
        tier="standard",
        question="测试问题",
        token_budget=1000,
        clarification=None,
        team_id="team-001",
        creator_id="user-001",
        trace_id="trace-001",
        session_factory=mock_factory,
        llm=None,
        retrieval_client=None,
        hub=hub,
    )


def _control_snapshot(run_id: str, stage: ResearchStage, used: int, draft: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "current_stage": stage,
        "token_used": used,
        "stage_attempts": {},
        "report_draft": draft,
        "report_claims": [],
        "conflicts": [],
        "report_outline": [],
    }


@pytest.mark.asyncio
async def test_run_research_async_pause_finalizes_paused() -> None:
    """AC-1：pause 信号在下一 await 点生效，落 paused 并发 run.finished(paused)。"""
    from datetime import UTC, datetime, timedelta

    run_id = "test-run-pause"
    registry = get_run_registry()
    registry.unregister(run_id)  # 防御性清理

    gate = asyncio.Event()
    run = ResearchRun(
        id=run_id,
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="standard",
        question="测试问题",
        status="running",
        current_stage="retrieve",
        token_budget=1000,
        token_used=100,
        started_at=datetime.now(tz=UTC) - timedelta(seconds=5),
    )
    # 收尾的独立连接读到 pause 端点已置位的 paused
    _mock_session, mock_factory, added = _build_control_mocks(run, "paused")
    hub = RealtimeHub()
    events, collect_task = await _collect_until_finished(hub, run_id)
    snapshot = _control_snapshot(run_id, ResearchStage.RETRIEVE, 100, "")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "app.orchestrator.executor._compile_for_deps",
            lambda deps, checkpointer=None: _GatedAsyncGraphStream([snapshot], gate),
        )
        task = asyncio.create_task(run_research_async(**_run_kwargs(run_id, mock_factory, hub)))
        # 等第一超步处理完（阶段帧 + 中间提交）
        await asyncio.sleep(0.1)
        assert registry.is_active(run_id)

        # 投递 pause 信号（端点的乐观置位由收尾会话 get 侧效模拟）
        assert registry.request_stop(run_id, "pause") is True
        await asyncio.wait_for(task, timeout=2.0)

    await asyncio.sleep(0.05)
    await _drain_collector(collect_task)
    assert registry.is_active(run_id) is False

    finished = [e for e in events if e["type"] == "run.finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "paused"
    # 暂停不是终态：不写 finished_at；token 末帧与快照一致
    assert run.finished_at is None
    token_frames = [e for e in events if e["type"] == "token.usage.update"]
    assert token_frames[-1]["payload"]["used"] == 100
    # 暂停路径不落报告（阶段行 add 属正常首跑补齐）
    assert [obj for obj in added if isinstance(obj, Report)] == []


@pytest.mark.asyncio
async def test_run_research_async_cancel_keeps_partial_draft() -> None:
    """AC-3/AC-4：cancel 落 cancelled；快照含草稿且 keep_partial 时落 draft 报告。"""
    from datetime import UTC, datetime, timedelta

    run_id = "test-run-cancel"
    registry = get_run_registry()
    registry.unregister(run_id)

    gate = asyncio.Event()
    finished_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    run = ResearchRun(
        id=run_id,
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="standard",
        question="测试问题",
        status="running",
        current_stage="report",
        token_budget=1000,
        token_used=500,
        started_at=datetime.now(tz=UTC) - timedelta(seconds=5),
        finished_at=finished_at,
    )
    # 收尾的独立连接读到 cancel 端点已置位的 cancelled
    _mock_session, mock_factory, added = _build_control_mocks(run, "cancelled")
    hub = RealtimeHub()
    events, collect_task = await _collect_until_finished(hub, run_id)
    snapshot = _control_snapshot(run_id, ResearchStage.REPORT, 500, "## 未完成的草稿报告")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "app.orchestrator.executor._compile_for_deps",
            lambda deps, checkpointer=None: _GatedAsyncGraphStream([snapshot], gate),
        )
        task = asyncio.create_task(run_research_async(**_run_kwargs(run_id, mock_factory, hub)))
        await asyncio.sleep(0.1)
        assert registry.request_stop(run_id, "cancel", keep_partial=True) is True
        await asyncio.wait_for(task, timeout=2.0)

    await asyncio.sleep(0.05)
    await _drain_collector(collect_task)
    assert registry.is_active(run_id) is False

    finished = [e for e in events if e["type"] == "run.finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "cancelled"
    assert finished[0]["partial_report_id"] is not None
    # 服务端已写的 finished_at 不被覆盖
    assert run.finished_at == finished_at
    # 草稿报告落库
    reports = [obj for obj in added if isinstance(obj, Report)]
    assert len(reports) == 1
    assert reports[0].status == "draft"
    assert "未完成的草稿报告" in reports[0].content_md


@pytest.mark.asyncio
async def test_run_research_async_cancel_without_partial_creates_no_report() -> None:
    """keep_partial=False 时即使快照含草稿也不落 Report。"""
    run_id = "test-run-cancel-no-partial"
    registry = get_run_registry()
    registry.unregister(run_id)

    gate = asyncio.Event()
    run = ResearchRun(
        id=run_id,
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="standard",
        question="测试问题",
        status="running",
        current_stage="report",
        token_budget=1000,
        token_used=300,
    )
    _mock_session, mock_factory, added = _build_control_mocks(run, "cancelled")
    hub = RealtimeHub()
    events, collect_task = await _collect_until_finished(hub, run_id)
    snapshot = _control_snapshot(run_id, ResearchStage.REPORT, 300, "## 草稿")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "app.orchestrator.executor._compile_for_deps",
            lambda deps, checkpointer=None: _GatedAsyncGraphStream([snapshot], gate),
        )
        task = asyncio.create_task(run_research_async(**_run_kwargs(run_id, mock_factory, hub)))
        await asyncio.sleep(0.1)
        assert registry.request_stop(run_id, "cancel", keep_partial=False) is True
        await asyncio.wait_for(task, timeout=2.0)

    await asyncio.sleep(0.05)
    await _drain_collector(collect_task)
    finished = [e for e in events if e["type"] == "run.finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "cancelled"
    assert "partial_report_id" not in finished[0]
    assert [obj for obj in added if isinstance(obj, Report)] == []


# ---------------------------------------------------------------------------
# M2-5 T2：澄清挂起 → answers 恢复不二次挂起 + interrupt.requested 帧
# ---------------------------------------------------------------------------


class _ClarificationGateLLM(LLMClient):
    """首次 clarify 判定需追问；恢复后 clarify 跳过，后续 schema 走默认结构化产出。"""

    def __init__(self, tokens_per_call: int = 60) -> None:
        # 跳过父类 __init__：避免熔断器/注册表副作用
        self.tokens_per_call = tokens_per_call
        self.clarify_calls = 0
        self.call_tags: list[str] = []

    async def complete_structured(  # type: ignore[override]
        self,
        *,
        schema: Any,
        messages: list[Any] | None = None,  # noqa: ARG002
        system_prompt: str | None = None,  # noqa: ARG002
        user_prompt: str | None = None,  # noqa: ARG002
        temperature: float | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
    ) -> StructuredCompletion:
        self.call_tags.append(",".join(tags or []))
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": self.tokens_per_call,
        }
        if schema is ClarificationSchema:
            self.clarify_calls += 1
            if self.clarify_calls == 1:
                return StructuredCompletion(
                    parsed=ClarificationSchema(
                        requires_user_input=True,
                        questions=[
                            {
                                "key": "scope",
                                "text": "请补充研究的时间范围",
                                "options": ["近一年", "近三年"],
                                "recommended": 1,
                            }
                        ],
                        defaults={},
                        structured_question={},
                    ),
                    usage=usage,
                    model="stub-clarify-gate",
                    raw="",
                )
            return StructuredCompletion(
                parsed=ClarificationSchema(
                    requires_user_input=False,
                    questions=[],
                    defaults={},
                    structured_question={"goal": "兜底目标", "scope": "", "key_concepts": []},
                ),
                usage=usage,
                model="stub-clarify-gate",
                raw="",
            )
        return StructuredCompletion(
            parsed=SubQuestionListSchema(
                sub_questions=[{"question": "澄清恢复验证子问题", "depends_on": [], "rationale": "测试"}]
            ),
            usage=usage,
            model="stub-clarify-gate",
            raw="",
        )


@pytest.mark.asyncio
async def test_clarification_pause_resume_with_answers_completes_once() -> None:
    """AC-5：澄清挂起 → interrupt.requested 帧 → answers 恢复后一路 succeeded，不二次挂起。"""
    run_id = "test-run-clarify-resume"
    run = ResearchRun(
        id=run_id,
        project_id="proj-001",
        creator_id="user-001",
        template_id="generic",
        tier="standard",
        question="光伏产业趋势如何",
        status="pending",
        token_budget=100_000,
    )

    mock_session = AsyncMock()
    mock_session.scalar.side_effect = [run, run, "team-001"]
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    _scalars_result = MagicMock()
    _scalars_result.all.return_value = []
    mock_session.scalars = AsyncMock(return_value=_scalars_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    hub = RealtimeHub()
    shared_saver = InMemorySaver()

    async def _collect_one() -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        async for event in hub.subscribe(f"runs:{run_id}"):
            collected.append(event)
            if event.get("type") == "run.finished":
                break
        return collected

    llm = _ClarificationGateLLM(tokens_per_call=60)

    collect_first = asyncio.create_task(_collect_one())
    await run_research_async(
        run_id=run_id,
        project_id="proj-001",
        template_id="generic",
        tier="standard",
        question="光伏产业趋势如何",
        token_budget=100_000,
        clarification=None,
        team_id="team-001",
        creator_id="user-001",
        trace_id="trace-clarify",
        session_factory=mock_factory,
        llm=llm,
        retrieval_client=_OfflineRetrievalClient(),
        hub=hub,
        checkpointer=shared_saver,
    )
    first_events = await collect_first

    # 首跑停在澄清挂起
    assert run.status == "paused"
    interrupt_events = [e for e in first_events if e["type"] == "interrupt.requested"]
    assert len(interrupt_events) == 1
    payload = interrupt_events[0]["payload"]
    assert payload["reason"] == "clarify"
    assert payload["questions"][0]["key"] == "scope"
    assert payload["expires_in_seconds"] == 900
    # 帧序：interrupt.requested 先于 run.finished(paused)
    type_order = [e["type"] for e in first_events]
    assert type_order.index("interrupt.requested") < type_order.index("run.finished")
    assert first_events[-1]["status"] == "paused"

    # 恢复：提交澄清答案
    collect_resume = asyncio.create_task(_collect_one())
    await asyncio.sleep(0)
    await resume_research_async(
        run_id=run_id,
        human_input={"answers": {"scope": "近三年"}},
        session_factory=mock_factory,
        checkpointer=shared_saver,
        llm=llm,
        retrieval_client=_OfflineRetrievalClient(),
        hub=hub,
    )
    resume_events = await collect_resume

    assert run.status == "succeeded"
    assert run.current_stage == "report"
    # 恢复后 clarify 节点被跳过（仅首跑那一次 LLM 澄清判定），未二次挂起
    assert llm.clarify_calls == 1
    assert not [e for e in resume_events if e["type"] == "interrupt.requested"]
    finished = [e for e in resume_events if e["type"] == "run.finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "succeeded"
