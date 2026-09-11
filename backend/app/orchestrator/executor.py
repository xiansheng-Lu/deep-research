"""研究编排执行器（WP-6.1）。

把 LangGraph ``StateGraph`` 与运行时依赖（DB 会话 / LLM / 检索客户端 / Hub）衔接起来，
作为 ``POST /runs`` 后台任务的唯一执行入口。

核心约定：
- 入口 ``run_research_async`` 由 ``asyncio.create_task`` 调度；不阻塞 API 响应。
- 节点依赖通过闭包绑定到 ``NodeDeps`` 后再装配图（与 ``graph.build_research_graph``
  共享同一份接线，单点注入 deps）。
- ``checkpointer=InMemorySaver()`` 仅用于 M1 端到端冒烟；M2 切到 ``AsyncPostgresSaver``。
- 执行结果通过 ``RealtimeHub`` 推送 ``runs:{run_id}`` 频道事件。
- 完成后 ``ResearchRun.status`` 写回 ``succeeded`` / ``failed``；``Report`` 在
  成功路径新建一条 ``draft``；失败路径不创建报告。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.edges import (
    decide_after_await_human,
    decide_after_clarify,
    decide_after_cost_checkpoint,
    decide_after_critique,
)
from app.orchestrator.nodes import (
    await_human,
    clarifier,
    cost_checkpoint,
    critic,
    failure_recovery,
    reporter,
    researcher_fan_out,
    standardizer,
    sub_questioner,
    user_intervention,
)
from app.orchestrator.state import ResearchStage, ResearchState
from app.realtime.hub import RealtimeHub

log = get_logger("orchestrator.executor")

# 与 graph.compile_research_graph 保持一致（interrupt_before HITL 挂起点）
_INTERRUPT_BEFORE: tuple[str, ...] = ("await_human", "user_intervention")


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _bind(node_run: Any, deps: NodeDeps) -> Any:
    """把 ``NodeDeps`` 闭包到节点 ``run`` 函数，绕过 LangGraph state-only 调用。"""

    async def _wrapped(state: ResearchState) -> dict[str, Any]:
        return await node_run(state, deps=deps)

    return _wrapped


def _build_graph_with_deps(deps: NodeDeps) -> Any:
    """复制 ``graph.build_research_graph`` 的接线，但节点 run 闭包绑定到 ``deps``。

    该函数是 executor 内部接线，与 ``graph.compile_research_graph`` 共享图结构，
    仅在节点 callable 上注入运行时依赖，避免污染生产入口。

    注意：只有签名含 ``deps`` 关键字的节点（clarifier / sub_questioner /
    researcher_fan_out / standardizer / critic / reporter）才闭包绑定；
    failure_recovery / await_human / cost_checkpoint / user_intervention
    为纯 state 节点，``run(state)`` 不接受 deps，直接注册。
    """
    graph = StateGraph(ResearchState)
    # 纯 state 节点：run(state)，不注入 deps
    graph.add_node("failure_recovery", failure_recovery.run)
    graph.add_node("await_human", await_human.run)
    graph.add_node("cost_checkpoint", cost_checkpoint.run)
    graph.add_node("user_intervention", user_intervention.run)
    # 依赖节点：run(state, deps=...)，闭包注入运行时依赖
    graph.add_node("clarify", _bind(clarifier.run, deps))
    graph.add_node("decompose", _bind(sub_questioner.run, deps))
    graph.add_node("retrieve", _bind(researcher_fan_out.run, deps))
    graph.add_node("standardize", _bind(standardizer.run, deps))
    graph.add_node("critique", _bind(critic.run, deps))
    graph.add_node("report", _bind(reporter.run, deps))

    graph.add_edge(START, "failure_recovery")
    graph.add_edge("failure_recovery", "clarify")
    graph.add_conditional_edges(
        "clarify",
        decide_after_clarify,
        {"await_human": "await_human", "decompose": "decompose"},
    )
    graph.add_edge("decompose", "retrieve")
    graph.add_edge("retrieve", "standardize")
    graph.add_edge("standardize", "critique")
    graph.add_conditional_edges(
        "critique",
        decide_after_critique,
        {"await_human": "await_human", "cost_checkpoint": "cost_checkpoint"},
    )
    graph.add_conditional_edges(
        "await_human",
        decide_after_await_human,
        {"clarify": "clarify", "critique": "critique"},
    )
    graph.add_conditional_edges(
        "cost_checkpoint",
        decide_after_cost_checkpoint,
        {"user_intervention": "user_intervention", "report": "report"},
    )
    graph.add_edge("user_intervention", "report")
    graph.add_edge("report", END)
    return graph


def _compile_for_deps(deps: NodeDeps) -> Any:
    """编译绑定 deps 后的可执行图。"""
    checkpointer = InMemorySaver()
    return _build_graph_with_deps(deps).compile(
        checkpointer=checkpointer,
        interrupt_before=list(_INTERRUPT_BEFORE),
    )


def _build_initial_state(
    *,
    run_id: str,
    project_id: str,
    question: str,
    template_id: str,
    tier: str,
    token_budget: int,
    clarification: dict[str, Any] | None,
    trace_id: str,
) -> ResearchState:
    """组装 ``ResearchState`` 初始值。"""
    return ResearchState(
        run_id=run_id,
        project_id=project_id,
        question=question,
        clarification=clarification,
        template_id=template_id,
        tier=tier,
        token_used=0,
        token_budget=token_budget,
        sub_questions=[],
        evidence=[],
        standardized_evidence=[],
        conflicts=[],
        verdicts=[],
        report_outline=[],
        report_claims=[],
        report_draft="",
        current_stage=ResearchStage.CLARIFY,
        stage_attempts={},
        human_input=None,
        started_at=_utcnow_iso(),
        finished_at=None,
        trace_id=trace_id,
        failure_reason=None,
        updated_at=_utcnow_iso(),
    )


async def _publish_event(hub: RealtimeHub, run_id: str, event: dict[str, Any]) -> None:
    """统一事件推送：失败不阻断主流程。"""
    try:
        await hub.publish(f"runs:{run_id}", event)
    except Exception as exc:  # noqa: BLE001
        log.warning("hub.publish 失败", extra={"run_id": run_id, "err": repr(exc)})


async def _load_run(session: AsyncSession, run_id: str) -> ResearchRun | None:
    return await session.scalar(select(ResearchRun).where(ResearchRun.id == run_id))


async def _mark_succeeded(
    session: AsyncSession,
    run: ResearchRun,
    *,
    final_state: ResearchState,
    report_markdown: str,
    template_id: str,
) -> None:
    """把 ResearchRun 标记为 succeeded 并新建 Report 行。"""
    now = datetime.now(tz=UTC)
    run.status = "succeeded"
    run.current_stage = ResearchStage.REPORT.value
    run.started_at = run.started_at or now
    run.finished_at = now
    run.token_used = int(final_state.get("token_used") or 0)
    run.error_code = None
    run.error_message = None

    report = Report(
        id=new_ulid(),
        run_id=run.id,
        template_id=template_id,
        status="draft",
        content_md=report_markdown,
        content_json={
            "claims": list(final_state.get("report_claims") or []),
            "conflicts": list(final_state.get("conflicts") or []),
            "outline": list(final_state.get("report_outline") or []),
        },
        token_used=int(final_state.get("token_used") or 0),
    )
    session.add(report)


async def _mark_failed(
    session: AsyncSession,
    run: ResearchRun,
    *,
    error_code: str,
    error_message: str,
    partial_state: ResearchState | None,
) -> None:
    """把 ResearchRun 标记为 failed 并写入错误码。"""
    now = datetime.now(tz=UTC)
    run.status = "failed"
    run.finished_at = now
    run.error_code = error_code
    run.error_message = error_message
    if partial_state is not None:
        run.token_used = int(partial_state.get("token_used") or 0)
        cur_stage = partial_state.get("current_stage")
        if isinstance(cur_stage, str):
            run.current_stage = cur_stage


async def run_research_async(
    *,
    run_id: str,
    project_id: str,
    template_id: str,
    tier: str,
    question: str,
    token_budget: int,
    clarification: dict[str, Any] | None,
    team_id: str,
    creator_id: str,
    trace_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    llm: Any,
    retrieval_client: Any,
    hub: RealtimeHub,
) -> None:
    """后台异步执行研究流程。

    Args:
        run_id: 已落库 ``ResearchRun`` 的 ULID。
        project_id: 归属项目 ID。
        template_id: 报告模板 ID；M1 阶段统一回退到通用模板。
        tier: 档位（quick / standard / deep / extreme）。
        question: 用户原始研究问题。
        token_budget: 档位对应的 token 预算上限。
        clarification: 澄清阶段合并结果（无澄清追问时为 None）。
        team_id / creator_id: 写入 ``NodeDeps`` 用于审计 / 限流。
        trace_id: 链路追踪 ID。
        session_factory: SQLAlchemy 异步 sessionmaker；用于加载/写回 ORM。
        llm / retrieval_client: 节点共享依赖；缺省时节点走降级路径。
        hub: 实时事件总线；阶段事件 / 终态事件由此推送。
    """
    deps = NodeDeps(
        run_id=run_id,
        team_id=team_id,
        trace_id=trace_id,
        llm=llm,
        retrieval_client=retrieval_client,
        db_session=None,
    )

    initial_state = _build_initial_state(
        run_id=run_id,
        project_id=project_id,
        question=question,
        template_id=template_id,
        tier=tier,
        token_budget=token_budget,
        clarification=clarification,
        trace_id=trace_id,
    )
    thread_config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

    # 让事件订阅者有机会注册（asyncio 调度顺序）
    await asyncio.sleep(0)

    async with session_factory() as session:
        run = await _load_run(session, run_id)
        if run is None:
            log.error("run 不存在", extra={"run_id": run_id})
            await _publish_event(
                hub,
                run_id,
                {"type": "run.failed", "run_id": run_id, "error_code": "RUN_NOT_FOUND"},
            )
            return

        # 标记运行中
        run.status = "running"
        run.started_at = datetime.now(tz=UTC)
        run.current_stage = ResearchStage.CLARIFY.value
        await session.flush()

        graph = _compile_for_deps(deps)
        final_state: ResearchState | None = None
        error_code = "INTERNAL_ERROR"
        error_message = ""
        try:
            final_state = await graph.ainvoke(initial_state, config=thread_config)
        except Exception as exc:  # noqa: BLE001
            log.exception("graph.ainvoke 失败", extra={"run_id": run_id})
            error_code = exc.__class__.__name__
            error_message = repr(exc)
            await _mark_failed(
                session,
                run,
                error_code=error_code,
                error_message=error_message,
                partial_state=None,
            )
        else:
            report_md = str(final_state.get("report_draft") or "")
            if report_md:
                await _mark_succeeded(
                    session,
                    run,
                    final_state=final_state,
                    report_markdown=report_md,
                    template_id=template_id,
                )
            else:
                # 无产出（如仍挂起在 HITL）→ 标记 paused
                run.status = "paused"
                cur_stage = final_state.get("current_stage")
                if isinstance(cur_stage, str):
                    run.current_stage = cur_stage
                run.finished_at = None

        await session.commit()

        finished_state = final_state if final_state is not None else initial_state
        finished_event: dict[str, Any] = {
            "type": "run.finished",
            "run_id": run_id,
            "status": run.status,
            "current_stage": run.current_stage,
            "token_used": int(finished_state.get("token_used") or 0),
            "occurred_at": _utcnow_iso(),
        }
        if run.status == "failed":
            finished_event["error_code"] = run.error_code
            finished_event["error_message"] = run.error_message
        await _publish_event(hub, run_id, finished_event)


__all__ = [
    "run_research_async",
    "_build_initial_state",
    "_compile_for_deps",
]
