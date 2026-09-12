"""研究编排执行器（WP-6.1）。

把 LangGraph ``StateGraph`` 与运行时依赖（DB 会话 / LLM / 检索客户端 / Hub）衔接起来，
作为 ``POST /runs`` 后台任务的唯一执行入口。

核心约定：
- 入口 ``run_research_async`` 由 ``asyncio.create_task`` 调度；不阻塞 API 响应。
- 节点依赖通过闭包绑定到 ``NodeDeps`` 后再装配图（与 ``graph.build_research_graph``
  共享同一份接线，单点注入 deps）。
- 检查点由调用方显式传入（生产为应用级 ``AsyncPostgresSaver``，thread_id=run_id，
  支持 HITL 跨请求/跨进程恢复，见 ``app.orchestrator.checkpoint``）；未传时退化为
  每次执行新建 ``InMemorySaver``（仅测试/遗留调用路径使用）。
- HITL 恢复入口 ``resume_research_async``：先把 ``human_input`` 写入线程状态，
  再以 ``Command(resume=...)`` 从 interrupt_before 挂起点续跑（§6.5.10 / §6.6）。
- 执行结果通过 ``RealtimeHub`` 推送 ``runs:{run_id}`` 频道事件。
- 完成后 ``ResearchRun.status`` 写回 ``succeeded`` / ``failed`` / ``paused``；
  ``Report`` 在成功路径新建一条 ``draft``；失败路径不创建报告。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.db.models.project import Project
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
    researcher_fan_out / standardizer / critic / reporter / await_human）才闭包
    绑定；failure_recovery / cost_checkpoint / user_intervention 为纯 state
    节点，``run(state)`` 不接受 deps，直接注册。
    """
    graph = StateGraph(ResearchState)
    # 纯 state 节点：run(state)，不注入 deps
    graph.add_node("failure_recovery", failure_recovery.run)
    graph.add_node("cost_checkpoint", cost_checkpoint.run)
    graph.add_node("user_intervention", user_intervention.run)
    # 依赖节点：run(state, deps=...)，闭包注入运行时依赖
    graph.add_node("await_human", _bind(await_human.run, deps))
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


def _compile_for_deps(
    deps: NodeDeps,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> Any:
    """编译绑定 deps 后的可执行图。

    Args:
        deps: 节点运行时依赖。
        checkpointer: 调用方持有的持久检查点（生产传应用级 PostgresSaver）；
            缺省时每次新建内存 saver，仅供测试隔离与遗留调用使用。
    """
    saver = checkpointer if checkpointer is not None else InMemorySaver()
    return _build_graph_with_deps(deps).compile(
        checkpointer=saver,
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


async def _astream_and_publish(
    graph: Any,
    initial_state: ResearchState,
    thread_config: dict[str, Any],
    *,
    run: ResearchRun,
    session: AsyncSession,
    hub: RealtimeHub,
    run_id: str,
) -> ResearchState | None:
    """流式执行图：逐 super-step 推送 ``stage.started`` 并同步 DB 阶段字段。

    - 使用 LangGraph ``astream``（values 模式）：每个 super-step 产出一份完整
      state 快照；仅当 ``current_stage`` 切换时推送一次阶段事件。
    - 同步把 ``ResearchRun.current_stage`` 落库并 flush，保证 WS 事件、DB
      轮询（GET /runs/{id}）与重连后的 GET 初帧三处阶段状态一致。

    Returns:
        最终（或挂起前最后一份）state 快照；图执行异常时向上抛出。
    """
    final_state: ResearchState | None = None
    last_stage: str | None = None
    # 必须显式指定 stream_mode="values"：当前 LangGraph 版本 astream 默认
    # "updates"（chunk 形如 {节点名: 增量patch}），取不到扁平 state 字段；
    # values 模式下每个 super-step 产出一份完整 state 快照。
    async for snapshot in graph.astream(
        initial_state,
        config=thread_config,
        stream_mode="values",
    ):
        final_state = snapshot
        # state 中 current_stage 可能是 ResearchStage(StrEnum) 或裸字符串，
        # 统一归一化为字符串，保证去重比对与 WS 载荷均为纯字符串契约。
        raw_stage = snapshot.get("current_stage")
        stage = raw_stage.value if isinstance(raw_stage, ResearchStage) else raw_stage
        if not isinstance(stage, str) or stage == last_stage:
            continue
        last_stage = stage
        run.current_stage = stage
        await session.flush()
        attempts = snapshot.get("stage_attempts") or {}
        await _publish_event(
            hub,
            run_id,
            {
                "type": "stage.started",
                "stage": stage,
                "current_stage": stage,
                "payload": {
                    "stage": stage,
                    "attempt": int(attempts.get(stage) or 1),
                    "token_used": int(snapshot.get("token_used") or 0),
                },
            },
        )
    return final_state


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
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> None:
    """后台异步执行研究流程（首次执行）。

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
        checkpointer: 应用级持久检查点；None 时退化为一次性内存 saver（测试用）。
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

        graph = _compile_for_deps(deps, checkpointer)
        await _drive_to_terminal(
            graph=graph,
            graph_input=initial_state,
            run=run,
            template_id=template_id,
            session=session,
            hub=hub,
            deps=deps,
            fallback_state=initial_state,
        )


async def _drive_to_terminal(
    *,
    graph: Any,
    graph_input: Any,
    run: ResearchRun,
    template_id: str,
    session: AsyncSession,
    hub: RealtimeHub,
    deps: NodeDeps,
    fallback_state: ResearchState | None,
) -> ResearchState | None:
    """驱动图执行到终态并完成落库与终态事件推送（首次执行/恢复共用）。

    - 节点依赖会话与执行器会话共享：节点侧落库与 run 状态写在同一事务边界；
    - 成功有报告 → succeeded + Report(draft)；无报告（HITL 挂起）→ paused；
      异常 → failed；三种终态统一提交后推送恰好一条 ``run.finished``。
    """
    run_id = run.id
    deps.db_session = session
    deps.hub = hub
    thread_config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

    final_state: ResearchState | None = None
    try:
        final_state = await _astream_and_publish(
            graph,
            graph_input,
            thread_config,
            run=run,
            session=session,
            hub=hub,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("graph.astream 执行失败", extra={"run_id": run_id})
        await _mark_failed(
            session,
            run,
            error_code=exc.__class__.__name__,
            error_message=repr(exc),
            partial_state=final_state,
        )
    else:
        # 未抛异常时 astream 必返回最后一份快照（空图才可能为 None，此处不会发生）
        assert final_state is not None
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
            # 挂起同样回写真实 token 用量：成本闸门触发时前端/轮询需看到
            # 已消耗量，否则预算治理在 paused 态失去可观测性
            run.token_used = int(final_state.get("token_used") or 0)
            cur_stage = final_state.get("current_stage")
            if isinstance(cur_stage, str):
                run.current_stage = cur_stage
            run.finished_at = None

    await session.commit()

    finished_state = final_state if final_state is not None else (fallback_state or {})
    finished_event: dict[str, Any] = {
        "type": "run.finished",
        "run_id": run_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "token_used": int(finished_state.get("token_used") or run.token_used),
        "occurred_at": _utcnow_iso(),
    }
    if run.status == "failed":
        finished_event["error_code"] = run.error_code
        finished_event["error_message"] = run.error_message
    await _publish_event(hub, run_id, finished_event)
    return final_state


async def resume_research_async(
    *,
    run_id: str,
    human_input: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: BaseCheckpointSaver[Any] | None,
    llm: Any,
    retrieval_client: Any,
    hub: RealtimeHub,
) -> None:
    """从 HITL 挂起点恢复研究（澄清答案 / 分歧裁决 / 纯继续）。

    图以 ``interrupt_before`` 挂起时，``Command(resume=...)`` 的负载不会自动并入
    图状态，因此先 ``aupdate_state`` 写入 ``human_input``，再从挂起线程续跑；
    ``await_human`` 节点随后按 ``interrupt_reason`` 分流回流（详设 §6.5.10）。

    仅 ``paused`` 状态的 run 可恢复；非挂起调用直接忽略（幂等保护）。
    """
    await asyncio.sleep(0)

    async with session_factory() as session:
        run = await _load_run(session, run_id)
        if run is None:
            log.error("恢复目标 run 不存在", extra={"run_id": run_id})
            await _publish_event(
                hub,
                run_id,
                {"type": "run.failed", "run_id": run_id, "error_code": "RUN_NOT_FOUND"},
            )
            return
        if run.status != "paused":
            log.warning(
                "非 paused 状态忽略恢复请求",
                extra={"run_id": run_id, "status": run.status},
            )
            return

        try:
            team_id = await session.scalar(select(Project.team_id).where(Project.id == run.project_id))
            deps = NodeDeps(
                run_id=run_id,
                team_id=str(team_id or ""),
                trace_id=run_id,
                llm=llm,
                retrieval_client=retrieval_client,
                db_session=None,
            )
            graph = _compile_for_deps(deps, checkpointer)
            thread_config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

            # 把人类输入显式写入挂起线程状态，供 await_human 节点读取
            await graph.aupdate_state(thread_config, {"human_input": human_input})

            run.status = "running"
            run.error_code = None
            run.error_message = None
            run.finished_at = None
            await session.flush()

            await _drive_to_terminal(
                graph=graph,
                graph_input=Command(resume=human_input),
                run=run,
                template_id=run.template_id,
                session=session,
                hub=hub,
                deps=deps,
                fallback_state=None,
            )
        except Exception as exc:  # noqa: BLE001 - 恢复护栏：任何异常都落 failed + 终态帧（TR-8.3）
            log.exception("HITL 恢复执行失败", extra={"run_id": run_id})
            await _mark_failed(
                session,
                run,
                error_code=exc.__class__.__name__,
                error_message=repr(exc),
                partial_state=None,
            )
            await session.commit()
            await _publish_event(
                hub,
                run_id,
                {
                    "type": "run.finished",
                    "run_id": run_id,
                    "status": "failed",
                    "current_stage": run.current_stage,
                    "error_code": run.error_code,
                    "error_message": run.error_message,
                    "occurred_at": _utcnow_iso(),
                },
            )


__all__ = [
    "run_research_async",
    "resume_research_async",
    "_drive_to_terminal",
    "_build_initial_state",
    "_compile_for_deps",
]
