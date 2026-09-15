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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.context import bind_run_context
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.db.base import new_ulid
from app.db.models.project import Project
from app.db.models.report import Report, ReportCitation
from app.db.models.run import ResearchRun, Stage
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
from app.orchestrator.persistence import (
    STAGE_ORDER,
    apply_stage_transition,
    ensure_stage_rows,
)
from app.orchestrator.registry import RunRegistryLike, get_run_registry
from app.orchestrator.state import ResearchStage, ResearchState
from app.quota.emitter import RunCostEmitter
from app.realtime.hub import RealtimeHub
from app.reporting.blocks import ReportAssembly

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


def _stage_duration_ms(row: Stage) -> int:
    """阶段行起止时间差（毫秒）；缺任一时间戳返回 0。"""
    if row.started_at is None or row.finished_at is None:
        return 0
    return max(0, int((row.finished_at - row.started_at).total_seconds() * 1000))


async def _publish_stage_finished(
    hub: RealtimeHub,
    run_id: str,
    *,
    row: Stage,
    token_used: int,
) -> None:
    """阶段收口帧（succeeded）；payload 对齐前端 StageFinishedPayload 超集。"""
    await _publish_event(
        hub,
        run_id,
        {
            "type": "stage.finished",
            "stage": row.name,
            "payload": {
                "stage": row.name,
                "attempt": int(row.attempt),
                "status": "succeeded",
                "duration_ms": _stage_duration_ms(row),
                "token_used": int(token_used),
            },
        },
    )


async def _publish_stage_failed(
    hub: RealtimeHub,
    run_id: str,
    *,
    stage: str,
    attempt: int,
    error_code: str | None,
    error_message: str | None,
) -> None:
    """run 失败终态帧：定位到 current_stage（M2-4 §7）。"""
    await _publish_event(
        hub,
        run_id,
        {
            "type": "stage.failed",
            "stage": stage,
            "payload": {
                "stage": stage,
                "attempt": int(attempt),
                "status": "failed",
                "error_code": error_code,
                "error_message": error_message,
                "retryable": False,
            },
        },
    )


@dataclass(slots=True)
class _StopHolder:
    """跨取消边界传递最后一份 state 快照（pause/cancel 收尾取 token 与草稿）。"""

    snapshot: ResearchState | None = None


async def _poll_stop_signal(registry: RunRegistryLike, run_id: str) -> None:
    """超步边界读取停止信号（M2-8b 跨进程）：有 pause/cancel 则对当前任务投递
    协作式取消，复用 M2-5 既有的 CancelledError 收尾路径。

    - 内存态：pause/cancel 端点已同步 ``task.cancel()``，此处为等价兜底；
    - Redis 态：信号由 API 进程写入 ``control:run:{id}``，worker 在此边界感知，
      不在节点执行中途打断（ask_followup 受理窗口仍只在 retrieve 扇出层边界，
      阶段方案 §16.3，M2-7 口径不回退）。
    """
    mode = await registry.peek_mode(run_id)
    if mode is None:
        return
    task = asyncio.current_task()
    assert task is not None
    log.info(
        "超步边界感知停止信号，投递协作式取消",
        extra={"run_id": run_id, "mode": mode},
    )
    task.cancel()
    # 立即让出控制权，使 CancelledError 在本 await 点生效，不再处理本帧
    await asyncio.sleep(0)


async def _astream_and_publish(
    graph: Any,
    initial_state: ResearchState | Command[Any],
    thread_config: dict[str, Any],
    *,
    run: ResearchRun,
    session: AsyncSession,
    hub: RealtimeHub,
    run_id: str,
    stage_rows: dict[str, Stage],
    cost_emitter: RunCostEmitter,
    stop_holder: _StopHolder,
    registry: RunRegistryLike,
) -> ResearchState | None:
    """流式执行图：逐 super-step 推进阶段行/帧、发射成本帧并做中间提交。

    - 使用 LangGraph ``astream``（values 模式）：每个 super-step 产出一份完整
      state 快照；``current_stage`` 切换时收口旧阶段（行置 succeeded +
      ``stage.finished`` 帧）并打开新阶段（行置 running + ``stage.started`` 帧）。
    - 每份快照交给 ``RunCostEmitter.observe``：写穿 ``run.token_used``、
      节流发 ``token.usage.update``、边沿发 ``cost.warning``。
    - 每个 super-step 处理完后 ``session.commit()``：看板 REST 与 WS 订阅者
      使用不同 DB 连接，运行中即可读到阶段/子问题/证据/成本（M2-4 §5.1）。

    Returns:
        最终（或挂起前最后一份）state 快照；图执行异常时向上抛出。
    """
    final_state: ResearchState | None = None
    last_stage: str | None = None
    latest_used = 0
    # resume 首帧待打开的新阶段：手动暂停后续跑时，首个 task 帧没有前置 values
    # 快照（latest_used 尚为 0），前序残留阶段行的收口必须等配对的 values 到达、
    # 拿到含暂停前消耗的真实累计后再做，故把「收口前序+打开新阶段」整体延迟一帧。
    pending_stage: str | None = None
    # 混合流：debug 的 task 事件先于节点执行（用于在节点真正开始时打开阶段，
    # 修复「阶段在节点完成后才戳记」导致的阶段耗时/受理窗口错位）；values
    # 提供每超步完整 state 快照（成本写穿、中间提交、取消快照、终态判定）。
    # 事件顺序实测为 task(N) → task_result(N) → values(post-N) → task(N+1)。
    async for mode, event in graph.astream(
        initial_state,
        config=thread_config,
        stream_mode=["debug", "values"],
    ):
        # 每个超步帧处理前先看停止信号：命中则本帧不再处理，协作收尾
        await _poll_stop_signal(registry, run_id)
        if mode == "debug":
            # 仅在六阶段业务节点「开始执行」时切换阶段行；failure_recovery /
            # await_human / user_intervention / cost_checkpoint 不产生阶段帧
            if event.get("type") != "task":
                continue
            payload = event.get("payload") or {}
            stage = payload.get("name")
            if not isinstance(stage, str) or stage not in STAGE_ORDER:
                continue
            if stage == last_stage:
                # resume 回流重跑同阶段（如 clarify）：不重开、不重发 started
                continue
            prior_running_rows = [
                prior
                for prior in STAGE_ORDER[: STAGE_ORDER.index(stage)]
                if (row := stage_rows.get(prior)) is not None and row.status == "running"
            ]
            has_stale_running = bool(prior_running_rows)
            if last_stage is None and has_stale_running:
                # resume 后首个业务帧且存在未收口的前序 running 行：挂起阶段超步
                # 已在检查点完成、节点不重放，首个 task 直接是更后阶段（如 clarify
                # 暂停后从 decompose 续跑）。此时尚无 values 快照、latest_used=0，
                # 不能在本帧收口残留行（会把 token 写成 0）；延迟到配对 values
                # 到达后统一处理。首跑首帧（前序行均 pending）不在此列，保持
                # task 帧立即打开阶段的 M2-4 受理窗口语义。
                pending_stage = stage
                continue
            # 流内正常推进：收口上一阶段单行（M2-4 语义），latest_used 为上一
            # 节点完成后的累计值（走到此分支 last_stage 必非空：None 且无残留
            # 行的首帧会落入下方同一打开路径，此时没有可收口的旧行）
            old_row = stage_rows.get(last_stage) if last_stage is not None else None
            if old_row is not None and old_row.status != "succeeded":
                apply_stage_transition(
                    old_row,
                    status="succeeded",
                    token_used=latest_used,
                    finished=True,
                )
                await _publish_stage_finished(hub, run_id, row=old_row, token_used=latest_used)
            attempts = (final_state or {}).get("stage_attempts") or {}
            attempt = int(attempts.get(stage) or 1)
            new_row = stage_rows.get(stage)
            if new_row is not None and new_row.status != "succeeded":
                apply_stage_transition(new_row, status="running", attempt=attempt)
            last_stage = stage
            run.current_stage = stage
            # 更新指标上下文：下一节点任务在 gather 创建时继承
            bind_run_context(stage=stage)
            await _publish_event(
                hub,
                run_id,
                {
                    "type": "stage.started",
                    "stage": stage,
                    "current_stage": stage,
                    "payload": {
                        "stage": stage,
                        "attempt": attempt,
                        "token_used": latest_used,
                    },
                },
            )
            # 立即提交：阶段开始即对外可见（ask_followup 受理窗口与真实检索
            # 执行区间一致，不再是节点完成后的毫秒级窗口）
            await session.commit()
            continue

        # mode == "values"：完整 state 快照（节点完成后产出）
        snapshot = cast(ResearchState, event)
        final_state = snapshot

        # resume 首帧延迟处理：values 到达后，先用真实累计收口排在新阶段之前
        # 仍 running 的残留行（M2-8b 交接 §7.4 P2），再打开新阶段——保证
        # finished(旧) 先于 started(新)，且收口 token 含暂停前已消耗的量。
        if pending_stage is not None:
            stage = pending_stage
            pending_stage = None
            snapshot_used = int(snapshot.get("token_used") or 0)
            for prior_stage in STAGE_ORDER[: STAGE_ORDER.index(stage)]:
                stale_row = stage_rows.get(prior_stage)
                if stale_row is not None and stale_row.status == "running":
                    apply_stage_transition(
                        stale_row,
                        status="succeeded",
                        token_used=snapshot_used,
                        finished=True,
                    )
                    await _publish_stage_finished(hub, run_id, row=stale_row, token_used=snapshot_used)
            attempts = snapshot.get("stage_attempts") or {}
            attempt = int(attempts.get(stage) or 1)
            opened_row = stage_rows.get(stage)
            if opened_row is not None and opened_row.status != "succeeded":
                apply_stage_transition(opened_row, status="running", attempt=attempt)
            last_stage = stage
            run.current_stage = stage
            bind_run_context(stage=stage)
            await _publish_event(
                hub,
                run_id,
                {
                    "type": "stage.started",
                    "stage": stage,
                    "current_stage": stage,
                    "payload": {"stage": stage, "attempt": attempt, "token_used": snapshot_used},
                },
            )
            await session.commit()
        # 记录最近完整快照：协作式取消落在本超步中途时，收尾据此取 token/草稿
        stop_holder.snapshot = snapshot
        used = int(snapshot.get("token_used") or 0)
        latest_used = used
        # 成本：写穿 run.token_used + 节流/边沿帧（不依赖事务提交）
        await cost_emitter.observe(used)
        # super-step 中间提交：运行中看板 REST 即可读到本步落库数据
        await session.commit()
    return final_state


async def _mark_succeeded(
    session: AsyncSession,
    run: ResearchRun,
    *,
    final_state: ResearchState,
    report_markdown: str,
    template_id: str,
    stage_rows: dict[str, Stage],
    assembly: ReportAssembly | None = None,
) -> None:
    """把 ResearchRun 标记为 succeeded、收口 report 阶段行并新建 final Report 行。

    M2-7：成功终稿落 ``status=final``，content_json 为结构化新形态
    （outline/blocks/citation_audit），同事务展开 report_citations 行。
    """
    now = datetime.now(tz=UTC)
    used = int(final_state.get("token_used") or 0)
    run.status = "succeeded"
    run.current_stage = ResearchStage.REPORT.value
    run.started_at = run.started_at or now
    run.finished_at = now
    run.token_used = used
    run.error_code = None
    run.error_message = None

    report_row = stage_rows.get(ResearchStage.REPORT.value)
    if report_row is not None:
        apply_stage_transition(
            report_row,
            status="succeeded",
            attempt=int(report_row.attempt or 1),
            token_used=used,
            finished=True,
        )

    if assembly is not None:
        audit = dict(assembly.audit)
        audit["reporter_degraded"] = bool(final_state.get("reporter_degraded"))
        content_json = {
            "outline": assembly.outline,
            "blocks": assembly.blocks,
            "citation_audit": audit,
        }
    else:
        # 防御路径（正常链路 reporter 必然产出装配）：空结构化终稿，仍标 final
        content_json = {"outline": [], "blocks": [], "citation_audit": {}}

    report = Report(
        id=new_ulid(),
        run_id=run.id,
        template_id=template_id,
        status="final",
        content_md=report_markdown,
        content_json=content_json,
        token_used=used,
    )
    session.add(report)

    if assembly is not None:
        # flush 取 report.id 后展开 block×证据 引文行（同事务，§5.9）
        await session.flush()
        for row in assembly.citation_rows:
            session.add(
                ReportCitation(
                    id=new_ulid(),
                    report_id=report.id,
                    block_id=row["block_id"],
                    evidence_id=row["evidence_id"],
                    claim_id=row.get("claim_id"),
                    position=row["position"],
                    snippet=row["snippet"],
                )
            )


async def _mark_failed(
    session: AsyncSession,
    run: ResearchRun,
    *,
    error_code: str,
    error_message: str,
    partial_state: ResearchState | None,
    stage_rows: dict[str, Stage] | None = None,
) -> None:
    """把 ResearchRun 标记为 failed，并把 current_stage 阶段行置 failed。"""
    now = datetime.now(tz=UTC)
    run.status = "failed"
    run.finished_at = now
    run.error_code = error_code
    run.error_message = error_message
    cur_stage: str | None = None
    used = 0
    attempts: dict[str, Any] = {}
    if partial_state is not None:
        used = int(partial_state.get("token_used") or 0)
        run.token_used = used
        raw_stage = partial_state.get("current_stage")
        cur_stage = raw_stage.value if isinstance(raw_stage, ResearchStage) else raw_stage
        if isinstance(cur_stage, str):
            run.current_stage = cur_stage
        raw_attempts = partial_state.get("stage_attempts")
        if isinstance(raw_attempts, dict):
            attempts = raw_attempts
    # 首份快照前即异常：沿用启动时写入的 current_stage（首次执行为 clarify，
    # 恢复路径为挂起阶段），保证当前阶段行一定收口为 failed
    if cur_stage is None and isinstance(run.current_stage, str):
        cur_stage = run.current_stage
    # 列默认值在 flush/insert 时才生效，尚未经过任何快照时实例属性仍为 None
    if run.token_used is None:
        run.token_used = 0
    if isinstance(cur_stage, str) and stage_rows is not None:
        row = stage_rows.get(cur_stage)
        if row is not None:
            apply_stage_transition(
                row,
                status="failed",
                attempt=int(attempts.get(cur_stage) or row.attempt or 1),
                token_used=used,
                finished=True,
                error_code=error_code,
            )


#: 受控取消条件更新允许的非终态状态
_STOPPABLE_STATES: tuple[str, ...] = ("pending", "running", "paused")


def _affected_rows(result: Any) -> int:
    """DML 执行结果的影响行数（async Result 需窄化为 CursorResult）。"""
    return cast(CursorResult[Any], result).rowcount


async def _finalize_controlled_stop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    hub: RealtimeHub,
    stop_holder: _StopHolder,
) -> str | None:
    """协作式取消的协程内收尾（M2-5）：按注册表 mode 落 paused/cancelled。

    在独立会话中执行（被取消的执行会话可能已随 ``async with`` 回滚），收尾后
    强制补发末帧 token 并推恰好一条 ``run.finished(status=paused|cancelled)``。
    自然终态（协程在取消信号生效前已完成 succeeded/failed 落库）不覆盖、不重发。

    Returns:
        最终状态（paused/cancelled）；自然终态/run 不存在返回 None。
    """
    registry = get_run_registry()
    mode = (await registry.consume_mode(run_id)) or "cancel"
    keep_partial = await registry.keep_partial_for(run_id)
    # 停止信号名 → run 终态状态名（pause→paused，cancel→cancelled）
    target_status = "paused" if mode == "pause" else "cancelled"

    async with session_factory() as session:
        run = await session.get(ResearchRun, run_id)
        if run is None:
            return None
        # 自然终态帧已由 _drive_to_terminal 正常路径发出
        if run.status in ("succeeded", "failed"):
            return None

        now = datetime.now(tz=UTC)
        snapshot = stop_holder.snapshot or {}
        # 取消可能落在 token 写穿之后、中间提交之前：取 DB 值与快照值的较大者
        used = max(int(run.token_used or 0), int(snapshot.get("token_used") or 0))
        report_id: str | None = None

        if mode == "cancel":
            result = await session.execute(
                update(ResearchRun)
                .where(ResearchRun.id == run_id, ResearchRun.status.in_(_STOPPABLE_STATES))
                .values(
                    status="cancelled",
                    finished_at=now,
                    updated_at=now,
                    token_used=used,
                )
            )
            if _affected_rows(result) == 0:
                # 并发已落终态（自然完成/被服务端置为非可取消态）：以 DB 为准
                await session.refresh(run)
                if run.status != "cancelled":
                    return None
            # 条件未命中但状态已是 cancelled（服务端已置位）：token 以快照对齐发帧
            run.status = "cancelled"
            run.finished_at = run.finished_at or now
            run.token_used = used
        else:
            # 软暂停：仅 running 可置 paused（控制端点已乐观置位时条件不命中属正常）
            result = await session.execute(
                update(ResearchRun)
                .where(ResearchRun.id == run_id, ResearchRun.status == "running")
                .values(status="paused", updated_at=now, token_used=used)
            )
            if _affected_rows(result) == 0:
                await session.refresh(run)
                if run.status == "cancelled":
                    # pause→cancel 并发升级：改发 cancelled 终态
                    target_status = "cancelled"
                    run.finished_at = run.finished_at or now
                elif run.status != "paused":
                    return None
            else:
                run.status = "paused"
            # 帧与写穿均以最近快照用量为准（取消可能落在提交前）
            run.token_used = used
            run.finished_at = None

        # keep_partial：最终态为 cancelled 且最后快照含非空报告草稿时保留为 draft
        if target_status == "cancelled" and keep_partial:
            report_markdown = str(snapshot.get("report_draft") or "")
            if report_markdown.strip():
                existing_id = await session.scalar(select(Report.id).where(Report.run_id == run_id))
                if existing_id is None:
                    report = Report(
                        id=new_ulid(),
                        run_id=run_id,
                        template_id=run.template_id,
                        status="draft",
                        content_md=report_markdown,
                        content_json={
                            "claims": list(snapshot.get("report_claims") or []),
                            "conflicts": list(snapshot.get("conflicts") or []),
                            "outline": list(snapshot.get("report_outline") or []),
                        },
                        token_used=used,
                    )
                    session.add(report)
                    await session.flush()
                    report_id = report.id

        await session.commit()

        # 终态成本帧与看板数字对齐（paused 同样补发，恢复/轮询读到一致值）
        final_used = int(run.token_used or 0)
        await RunCostEmitter(hub=hub, run=run).observe(final_used, force=True)
        await _publish_event(
            hub,
            run_id,
            {
                "type": "run.finished",
                "run_id": run_id,
                "status": target_status,
                "current_stage": run.current_stage,
                "token_used": final_used,
                "occurred_at": _utcnow_iso(),
                **({"partial_report_id": report_id} if report_id else {}),
            },
        )
        return target_status


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
    owner_id: str | None = None,
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
        owner_id: Redis 态下的租约持有者标识（Celery task_id）；缺省时以当前
            asyncio 任务为句柄（内存态/单进程）。
    """
    registry = get_run_registry()
    current_task = asyncio.current_task()
    assert current_task is not None
    owner_ref: Any = current_task if owner_id is None else owner_id
    # M2-5：登记在途句柄，pause/cancel 控制端点据此投递协作式取消信号；
    # M2-8b Redis 态即对 lease:run:{id} 的 NX 抢占
    stop_holder = _StopHolder()
    # 返回值忽略：首跑为 run 的第一个驱动者，正常无旧句柄；恢复占位场景
    # 不会走到首跑入口
    prior = await registry.register(run_id, owner_ref)
    if registry.is_redis_backed and prior is not None:
        # 租约 NX 失败（任务重投递/重复投递，B-AC-6）：已有执行者持约，
        # 本次未抢到租约，直接安全退出不驱动图；finally 中 is_owner 为假，
        # 不会误放他人租约/误清信号。
        log.warning("run 租约已被持有，本次执行幂等退出", extra={"run_id": run_id})
        return
    try:
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

            # 标记运行中并补齐六阶段行（5 pending + clarify 由首个快照置 running）
            run.status = "running"
            run.started_at = datetime.now(tz=UTC)
            run.current_stage = ResearchStage.CLARIFY.value
            stage_rows = await ensure_stage_rows(session, run_id=run_id)
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
                stage_rows=stage_rows,
                stop_holder=stop_holder,
                registry=registry,
            )
    except asyncio.CancelledError:
        # 受控停止（pause/cancel）：仅当句柄仍属于本协程时收尾落库并发终态帧；
        # 被恢复占位/恢复协程接管时静默退出（暂停状态已由控制服务提交，
        # 后续由恢复链路接管）
        if await registry.is_owner(run_id, owner_ref):
            await _finalize_controlled_stop(
                session_factory,
                run_id=run_id,
                hub=hub,
                stop_holder=stop_holder,
            )
    finally:
        # 终态帧已发布后再清信号、释放租约（阶段方案 §16.1 顺序）
        if await registry.is_owner(run_id, owner_ref):
            await registry.clear_stop_signal(run_id)
        await registry.unregister(run_id, owner=owner_ref)


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
    stage_rows: dict[str, Stage],
    stop_holder: _StopHolder,
    registry: RunRegistryLike,
) -> ResearchState | None:
    """驱动图执行到终态并完成落库与终态事件推送（首次执行/恢复共用）。

    - 节点依赖会话与执行器会话共享：节点侧落库与 run 状态在同一事务边界，
      每个 super-step 中间提交一次（M2-4 §5.1）；
    - 成功有报告 → succeeded + Report(draft) + report 阶段收口帧；
      无报告（HITL 挂起）→ paused（当前阶段行保持 running）；
      异常 → failed + 当前阶段行 failed + ``stage.failed`` 帧；
    - 三种结局统一提交后补发强制收尾 token 帧，再推恰好一条 ``run.finished``。
    """
    run_id = run.id
    deps.db_session = session
    deps.hub = hub
    thread_config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

    # 绑定指标上下文：LLMClient 打点时据此带 model/stage/run_id 标签；
    # 新 run 在标记 running 时 current_stage 已是 clarify，恢复 run 为挂起阶段
    bind_run_context(
        run_id=run_id,
        stage=run.current_stage if isinstance(run.current_stage, str) else ResearchStage.CLARIFY.value,
    )

    cost_emitter = RunCostEmitter(hub=hub, run=run)
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
            stage_rows=stage_rows,
            cost_emitter=cost_emitter,
            stop_holder=stop_holder,
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001
        # Redis 态（Celery worker）：瞬时 Provider 故障不落 failed，向上抛交
        # 调度器有限重试（§16.4/B-AC-7）；中间超步已幂等提交，续跑从检查点
        # 接上。内存态无调度器重试，仍按既有口径直接收敛 failed。
        if registry.is_redis_backed and isinstance(exc, ProviderUnavailableError):
            log.warning(
                "Provider 瞬时不可用，交调度器有限重试，本超步不落 failed",
                extra={"run_id": run_id, "error": repr(exc)},
            )
            raise
        log.exception("graph.astream 执行失败", extra={"run_id": run_id})
        await _mark_failed(
            session,
            run,
            error_code=exc.__class__.__name__,
            error_message=repr(exc),
            partial_state=final_state,
            stage_rows=stage_rows,
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
                stage_rows=stage_rows,
                assembly=getattr(deps, "report_assembly", None),
            )
        else:
            # 无产出（如仍挂起在 HITL）→ 标记 paused；当前阶段行保持 running，
            # 暂停语义由 research_runs.status=paused 承载（Stage 枚举无 paused）
            run.status = "paused"
            # 挂起同样回写真实 token 用量：成本闸门触发时前端/轮询需看到
            # 已消耗量，否则预算治理在 paused 态失去可观测性
            run.token_used = int(final_state.get("token_used") or 0)
            cur_stage = final_state.get("current_stage")
            if isinstance(cur_stage, str):
                run.current_stage = cur_stage
            run.finished_at = None

    await session.commit()

    # 澄清挂起：在 run.finished(paused) 之前推 interrupt.requested（M2-5 §5.4）。
    # critique 挂起已有 conflict.detected、成本挂起由成本卡 danger 表达，均不发本帧。
    if run.status == "paused" and final_state is not None:
        interrupt_reason = final_state.get("interrupt_reason")
        interrupt_payload = final_state.get("interrupt_payload")
        if interrupt_reason == "clarify" and isinstance(interrupt_payload, dict):
            questions = interrupt_payload.get("questions")
            if isinstance(questions, list) and questions:
                await _publish_event(
                    hub,
                    run_id,
                    {
                        "type": "interrupt.requested",
                        "stage": "clarify",
                        "payload": {
                            "reason": "clarify",
                            "questions": questions,
                            "defaults": interrupt_payload.get("defaults", {}),
                            "expires_in_seconds": get_settings().clarification_expires_seconds,
                        },
                    },
                )

    # 终态阶段帧（提交后发出，帧内实体与 REST 此时已同源）
    if run.status == "succeeded":
        report_row = stage_rows.get(ResearchStage.REPORT.value)
        if report_row is not None:
            await _publish_stage_finished(hub, run_id, row=report_row, token_used=int(run.token_used))
    elif run.status == "failed":
        failed_stage = run.current_stage if isinstance(run.current_stage, str) else None
        failed_row = stage_rows.get(failed_stage) if failed_stage is not None else None
        await _publish_stage_failed(
            hub,
            run_id,
            stage=failed_stage or "",
            attempt=int(failed_row.attempt) if failed_row is not None else 1,
            error_code=run.error_code,
            error_message=run.error_message,
        )

    # 终态/挂起点强制补发一帧 token.usage.update，保证收尾数字与 run 行一致
    final_used = int(run.token_used or 0)
    await cost_emitter.observe(final_used, force=True)

    finished_state = final_state if final_state is not None else (fallback_state or {})
    finished_event: dict[str, Any] = {
        "type": "run.finished",
        "run_id": run_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "token_used": int(finished_state.get("token_used") or final_used),
        "occurred_at": _utcnow_iso(),
    }
    if run.status == "failed":
        finished_event["error_code"] = run.error_code
        finished_event["error_message"] = run.error_message
    await _publish_event(hub, run_id, finished_event)
    return final_state


async def read_run_interrupt(
    *,
    run_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: BaseCheckpointSaver[Any] | None,
    llm: Any,
    retrieval_client: Any,
) -> dict[str, Any] | None:
    """只读挂起线程的 interrupt_reason/interrupt_payload（M2-5）。

    用 ``aget_state`` 读检查点而不驱动图执行，供 resume 载荷校验与
    ``GET /runs/{id}`` 的澄清卡刷新恢复使用；无挂起上下文返回 None。
    """
    async with session_factory() as session:
        run = await _load_run(session, run_id)
        if run is None:
            return None
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
        state_snapshot = await graph.aget_state(thread_config)
        values: dict[str, Any] = dict(getattr(state_snapshot, "values", None) or {})
        reason = values.get("interrupt_reason")
        payload = values.get("interrupt_payload")
        if reason is None and not isinstance(payload, dict):
            return None
        return {
            "reason": str(reason) if reason else None,
            "payload": payload if isinstance(payload, dict) else {},
        }


async def resume_research_async(
    *,
    run_id: str,
    human_input: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: BaseCheckpointSaver[Any] | None,
    llm: Any,
    retrieval_client: Any,
    hub: RealtimeHub,
    owner_id: str | None = None,
) -> None:
    """从 HITL 挂起点恢复研究（澄清答案 / 分歧裁决 / 纯继续）。

    图以 ``interrupt_before`` 挂起时，``Command(resume=...)`` 的负载不会自动并入
    图状态，因此先 ``aupdate_state`` 写入 ``human_input``，再从挂起线程续跑；
    ``await_human`` 节点随后按 ``interrupt_reason`` 分流回流（详设 §6.5.10）。

    两种合法入口：

    - runs_control 恢复服务：服务层已乐观置 running 并在注册表预留占位，
      本协程接管占位续跑（生产 HTTP 路径）；
    - 裁决等直连入口：run 仍为 paused，由本协程自行翻转为 running。

    终态/pending 一律忽略；双击/重复恢复由服务层条件 UPDATE + 恢复占位拦截。
    """
    registry = get_run_registry()
    current_task = asyncio.current_task()
    assert current_task is not None
    owner_ref: Any = current_task if owner_id is None else owner_id
    # 恢复任务同样登记句柄：恢复途中允许再 pause/cancel。register 会取代
    # 恢复服务预先写入的占位（或暂停首跑协程的旧句柄），并沿用占位上已到达
    # 的停止信号；Redis 态为对租约的 NX 抢占。
    stop_holder = _StopHolder()
    prior = await registry.register(run_id, owner_ref)
    if registry.is_redis_backed and prior is not None:
        # 恢复任务重投递且租约已被持有：幂等退出，不等待进程外任务、不驱动图
        log.warning("resume 租约已被持有，本次恢复幂等退出", extra={"run_id": run_id})
        return
    try:
        # 被暂停的首跑协程可能仍在 CancelledError 收尾：等待其退出，避免两个
        # 驱动循环并发；旧协程经属主检查会自行放弃落库收尾（暂停状态已由
        # pause 服务提交）。
        if (
            prior is not None
            and prior.task is not None
            and prior.task is not current_task
            and not prior.task.done()
        ):
            await asyncio.gather(prior.task, return_exceptions=True)

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
            if run.status not in ("paused", "running"):
                # 终态/pending：忽略恢复（双击由服务层条件 UPDATE 拦截，
                # 此处兜底终态/pending 的直接/延迟调用）
                log.warning(
                    "非 paused/running 状态忽略恢复请求",
                    extra={"run_id": run_id, "status": run.status},
                )
                return
            # 微窗口内 pause/cancel 已落在恢复占位上：不驱动图，直接按信号
            # 收尾（DB 已由控制服务置 paused/cancelled）
            pending_mode = await registry.peek_mode(run_id)
            if pending_mode is not None:
                log.info(
                    "恢复协程启动前已收到停止信号，按受控停止收尾",
                    extra={"run_id": run_id, "mode": pending_mode},
                )
                await _finalize_controlled_stop(
                    session_factory,
                    run_id=run_id,
                    hub=hub,
                    stop_holder=stop_holder,
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

                # runs_control 恢复服务已乐观置 running；裁决直连入口下 run
                # 仍为 paused，由执行器在此翻转为 running
                if run.status == "paused":
                    run.status = "running"
                run.error_code = None
                run.error_message = None
                run.finished_at = None
                # 恢复路径同样补齐六阶段行（幂等取回既有行，succeeded 不回退）
                stage_rows = await ensure_stage_rows(session, run_id=run_id)
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
                    stage_rows=stage_rows,
                    stop_holder=stop_holder,
                    registry=registry,
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
    except asyncio.CancelledError:
        # 恢复途中受控停止：仅当句柄仍属于本协程时收尾；被更新的恢复任务或
        # 占位接管时本协程静默退出，不做落库/发帧（避免覆盖新状态）
        if await registry.is_owner(run_id, owner_ref):
            await _finalize_controlled_stop(
                session_factory,
                run_id=run_id,
                hub=hub,
                stop_holder=stop_holder,
            )
    finally:
        if await registry.is_owner(run_id, owner_ref):
            await registry.clear_stop_signal(run_id)
        await registry.unregister(run_id, owner=owner_ref)


__all__ = [
    "run_research_async",
    "resume_research_async",
    "read_run_interrupt",
    "_drive_to_terminal",
    "_build_initial_state",
    "_compile_for_deps",
]
