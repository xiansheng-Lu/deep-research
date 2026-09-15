"""研究运行控制域服务（M2-5）：软暂停与硬取消。

- ``pause_run``：仅 running 且存在在途执行者时受理；条件 UPDATE 乐观置 paused 后
  经注册表投递协作式取消信号，协程在 super-step 边界落 paused 并发终态帧。
  Redis 态下 running 但租约过期（worker 失联孤儿）返回 409
  ``RUN_ORPHAN_RECOVERING``，由 worker 启动清扫自动收敛（M2-8b §16.5/B-AC-9）。
- ``cancel_run``：pending/running/paused 可取消，终态（succeeded/failed/cancelled）
  幂等返回当前状态；有在途协程时由协程收尾落库发帧，无协程（如暂停孤儿）时
  服务端直接置终态并发帧。

归属校验由路由层复用 ``app.services.conflicts.get_owned_run`` 完成（不存在/非
创建者统一 404，不泄漏资源存在性）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.base import AuditAction
from app.audit.logger import write_audit_entry
from app.core.exceptions import ConflictError, ValidationError
from app.core.logging import get_logger
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.orchestrator.registry import RunRegistryLike, get_run_registry
from app.realtime.hub import get_hub
from app.schemas.runs import HumanInput

if TYPE_CHECKING:
    from fastapi import Request

    from app.realtime.hub import RealtimeHub

log = get_logger("services.runs_control")

#: 允许取消的非终态状态
_CANCELLABLE_STATES = ("pending", "running", "paused")


def _affected_rows(result: Any) -> int:
    """DML 执行结果的影响行数（async Result 需窄化为 CursorResult）。"""
    return cast(CursorResult[Any], result).rowcount


async def _find_report_id(session: AsyncSession, run_id: str) -> str | None:
    """查 run 已存在的报告 id（cancel 幂等/保留草稿时回填响应）。"""
    return cast(
        "str | None",
        await session.scalar(select(Report.id).where(Report.run_id == run_id)),
    )


async def pause_run(
    session: AsyncSession,
    *,
    run: ResearchRun,
    registry: RunRegistryLike,
    team_id: str,
    user_id: str,
    reason: str | None = None,
) -> ResearchRun:
    """软暂停运行中的研究；返回刷新后的 run（status=paused）。"""
    current_status = str(run.status)
    if current_status == "paused":
        raise ConflictError("研究运行已处于暂停状态", details={"code": "RUN_ALREADY_PAUSED"})
    if current_status != "running":
        raise ConflictError("当前研究运行状态不允许暂停", details={"code": "RUN_NOT_PAUSABLE"})
    if not await registry.is_active(run.id):
        # running 行但无在途执行者：拒绝而非制造「暂停后永远停在 running」的假成功。
        # Redis 态租约过期即 worker 失联孤儿，worker 启动清扫会自动收敛
        # （§16.5），返回专属码让前端提示刷新；内存态（单进程/离线测试）保持
        # M2-5 原码 RUN_NOT_PAUSABLE。
        if registry.is_redis_backed:
            raise ConflictError(
                "任务执行进程失联，系统正在自动恢复，请刷新后重试",
                details={"code": "RUN_ORPHAN_RECOVERING"},
            )
        raise ConflictError("当前研究运行状态不允许暂停", details={"code": "RUN_NOT_PAUSABLE"})

    now = datetime.now(tz=UTC)
    result = await session.execute(
        update(ResearchRun)
        .where(ResearchRun.id == run.id, ResearchRun.status == "running")
        .values(status="paused", updated_at=now)
    )
    if _affected_rows(result) == 0:
        # 条件未命中：协程恰在信号前置终态或已被并发暂停
        await session.refresh(run)
        final_status = str(run.status)
        if final_status == "paused":
            raise ConflictError("研究运行已处于暂停状态", details={"code": "RUN_ALREADY_PAUSED"})
        raise ConflictError("当前研究运行状态不允许暂停", details={"code": "RUN_NOT_PAUSABLE"})

    await write_audit_entry(
        session,
        team_id=team_id,
        user_id=user_id,
        action=AuditAction.RUN_PAUSE,
        target_type="run",
        target_id=run.id,
        payload={"reason": reason} if reason else None,
    )
    await session.commit()
    await session.refresh(run)
    # 乐观置位已提交；协程收尾时以 DB 状态为准补帧，信号投递失败不影响暂停事实
    await registry.request_stop(run.id, "pause")
    log.info("研究运行已软暂停", extra={"run_id": run.id})
    return run


async def cancel_run(
    session: AsyncSession,
    *,
    run: ResearchRun,
    registry: RunRegistryLike,
    hub: RealtimeHub | None,
    team_id: str,
    user_id: str,
    keep_partial: bool = True,
    reason: str | None = None,
) -> tuple[ResearchRun, str | None]:
    """硬取消研究；返回 (刷新后的 run, 已存在报告 id 或缺省)。对终态幂等。"""
    # 终态：幂等返回当前状态，不重复发帧、不改状态、不重复记审计
    if str(run.status) in ("succeeded", "failed", "cancelled"):
        return run, await _find_report_id(session, run.id)

    now = datetime.now(tz=UTC)
    result = await session.execute(
        update(ResearchRun)
        .where(ResearchRun.id == run.id, ResearchRun.status.in_(_CANCELLABLE_STATES))
        .values(status="cancelled", finished_at=now, updated_at=now)
    )
    if _affected_rows(result) == 0:
        # 并发下协程刚好自然终态：以 DB 真实状态幂等返回
        await session.refresh(run)
        return run, await _find_report_id(session, run.id)

    await write_audit_entry(
        session,
        team_id=team_id,
        user_id=user_id,
        action=AuditAction.RUN_CANCEL,
        target_type="run",
        target_id=run.id,
        payload={"reason": reason, "keep_partial": keep_partial},
    )
    await session.commit()
    await session.refresh(run)
    signalled = await registry.request_stop(run.id, "cancel", keep_partial=keep_partial)
    partial_report_id = await _find_report_id(session, run.id)

    if not signalled:
        # 无在途协程（paused 孤儿/句柄已摘除）：服务端直接发终态帧收敛看板
        await _publish_cancelled(hub, run)
        log.info("研究运行已硬取消（无在途协程，服务端直接终态）", extra={"run_id": run.id})
    else:
        log.info("研究运行取消信号已投递，协程收尾中", extra={"run_id": run.id})
    return run, partial_report_id


async def resume_run(
    session: AsyncSession,
    *,
    run: ResearchRun,
    human_input: HumanInput | None,
    user_id: str,
    team_id: str,
    request: Request,
) -> ResearchRun:
    """从暂停/挂起点恢复研究（M2-5 §5.3）。

    - 仅 paused 可恢复（409 RUN_NOT_RESUMABLE）；
    - 澄清挂起（checkpoint 中 interrupt_reason=clarify）必须携带非空 answers，
      纯继续 422；裁决/成本/手动暂停允许 kind=proceed；
    - action 分支与 POST /intervene 等价，T3 队列落地前 409；
    - 条件 UPDATE 抢占 paused→running 后异步调度 resume_research_async。
    """
    if str(run.status) != "paused":
        raise ConflictError("当前研究运行状态不允许恢复", details={"code": "RUN_NOT_RESUMABLE"})

    # 延迟导入：避免服务层在纯 schema 使用环境强耦合编排依赖
    from app.orchestrator.executor import read_run_interrupt, resume_research_async

    # lifespan 保证注入；直接属性访问取得 Any 类型（与 runs.py 调度口径一致）
    factory = request.app.state.session_factory
    checkpointer = getattr(request.app.state, "checkpointer", None)
    llm = request.app.state.llm
    retrieval_client = request.app.state.retrieval_client
    hub = getattr(request.app.state, "hub", None) or get_hub()

    interrupt = await read_run_interrupt(
        run_id=run.id,
        session_factory=factory,
        checkpointer=checkpointer,
        llm=llm,
        retrieval_client=retrieval_client,
    )
    reason = str((interrupt or {}).get("reason") or "")

    # 归一载荷：空 body → 纯继续
    normalized: dict[str, Any]
    if human_input is None or human_input.kind == "proceed":
        if reason == "clarify":
            raise ValidationError("澄清挂起必须提交澄清答案，不能纯继续")
        normalized = {"kind": "proceed"}
    elif human_input.action is not None:
        # T3：主动介入队列落地后开放（running/paused 统一经 /intervene）
        raise ConflictError(
            "主动介入动作请走 POST /runs/{id}/intervene",
            details={"code": "INTERVENE_NOT_ALLOWED"},
        )
    else:
        answers = dict(human_input.answers or {})
        if reason == "clarify":
            real_answers = {k: v for k, v in answers.items() if k != "verdicts"}
            if not real_answers or not any(isinstance(v, str) and v.strip() for v in real_answers.values()):
                raise ValidationError("至少回答一个澄清问题")
        # 裁决答案注入当前用户（与 /conflicts/verdict 自动恢复同构）
        verdicts = answers.get("verdicts")
        if isinstance(verdicts, dict):
            for verdict in verdicts.values():
                if isinstance(verdict, dict):
                    verdict["user_id"] = user_id
        normalized = {"answers": answers}

    # 调度防重一：注册表层同步抢占恢复位，必须在条件 UPDATE 之前占位，
    # 使 pause/cancel 落在「预翻转→恢复协程注册」微窗口的信号不丢失。
    # 既有句柄若为已收到停止信号、正在收尾的首跑协程，属合法交接；
    # 其余在途句柄（已存在恢复占位/恢复协程）按重复恢复拒绝。
    registry = get_run_registry()
    prior = await registry.reserve(run.id)
    if prior is not None and not _is_finalizing_first_run(prior):
        # 已存在恢复协程等非收尾句柄：放回旧句柄，本次抢占作废
        await registry.restore(run.id, prior)
        raise ConflictError(
            "研究运行恢复已在调度中，请勿重复恢复",
            details={"code": "RUN_NOT_RESUMABLE"},
        )

    try:
        # 调度防重二：UPDATE 抢占 paused→running（双击以 affected_rows=0 拒绝）
        now = datetime.now(tz=UTC)
        result = await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == run.id, ResearchRun.status == "paused")
            .values(
                status="running",
                updated_at=now,
                error_code=None,
                error_message=None,
                finished_at=None,
            )
        )
        if _affected_rows(result) == 0:
            # 并发恢复/状态变化：以 DB 真实状态为准
            raise ConflictError(
                "当前研究运行状态不允许恢复",
                details={"code": "RUN_NOT_RESUMABLE"},
            )

        await write_audit_entry(
            session,
            team_id=team_id,
            user_id=user_id,
            action=AuditAction.RUN_RESUME,
            target_type="run",
            target_id=run.id,
            payload={"branch": "proceed" if "kind" in normalized else "answers"},
        )
        await session.commit()
    except Exception:
        # 抢占未成功：释放本请求的恢复占位（仅当句柄仍是占位），避免恢复位卡死
        if await registry.is_reserved(run.id):
            await registry.unregister(run.id)
        raise

    # 调度恢复：Redis 态先 DEL 旧控制键（pause 信号随恢复作废，cancel 无法到
    # paused 行；双击防护由条件 UPDATE + 租约 NX 承接，§16.3），再投 Celery；
    # 内存态保持进程内 create_task。
    if registry.is_redis_backed:
        await registry.clear_stop_signal(run.id)

        from app.workers.tasks.research import resume_run as resume_run_task

        resume_run_task.delay(run.id, normalized)
    else:
        asyncio.create_task(
            resume_research_async(
                run_id=run.id,
                human_input=normalized,
                session_factory=factory,
                checkpointer=checkpointer,
                llm=llm,
                retrieval_client=retrieval_client,
                hub=hub,
            )
        )
    log.info("研究运行已调度恢复", extra={"run_id": run.id, "reason": reason or "manual"})
    # 响应语义：恢复已抢占（200 running）；最终成败以帧/REST 收敛
    run.status = "running"
    return run


def _is_finalizing_first_run(handle: Any) -> bool:
    """既有句柄是否为「被暂停、正在收尾的首跑协程」（允许恢复占位取代）。

    pause 服务乐观提交后、首跑协程 CancelledError 收尾完成前调用 resume 时，
    句柄仍指向真实任务但已带停止信号（mode 非空）；这是合法的暂停→恢复
    交接，不是重复恢复。
    """
    return handle.task is not None and handle.mode is not None


async def _publish_cancelled(hub: RealtimeHub | None, run: ResearchRun) -> None:
    """无协程收尾路径的 cancelled 终态帧；hub 缺省静默。"""
    if hub is None:
        return
    try:
        await hub.publish(
            f"runs:{run.id}",
            {
                "type": "run.finished",
                "run_id": run.id,
                "status": "cancelled",
                "current_stage": run.current_stage,
                "token_used": int(run.token_used or 0),
            },
        )
    except Exception as exc:  # noqa: BLE001 - 终态帧失败不阻断取消
        log.warning(
            "cancelled 终态帧推送失败",
            extra={"run_id": run.id, "error": repr(exc)},
        )


__all__ = ["pause_run", "cancel_run", "resume_run"]
