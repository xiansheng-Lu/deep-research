"""孤儿 run 清扫（M2-8b §16.5）。

worker 进程启动处理第一个任务前扫一次（prefork 下每子进程一次）：扫描
``pending/running`` 且租约镜像过期（超过 ORPHAN_GRACE_SECONDS 未心跳）的 run，
按三类收敛：

1. PostgresSaver checkpoint 存在未消费 interrupt（澄清/裁决挂起）→ 置
   ``paused``，best-effort 补发 ``run.finished(paused)``；
2. 已有报告行：final → ``succeeded``；draft → ``cancelled``（保留草稿），
   以报告为准收敛终态并补帧；
3. 无报告无挂起 → 置 ``failed``，error_code=``RUN_WORKER_LOST`` 补帧。

``pending``（任务投递后 worker 即全灭、从未启动）先重投一次 execute_run，
Redis 计数器控制上限（默认 1 次），超限同样置 failed。全部收敛写
``run.orphan_recovered`` 审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update

from app.audit.base import AuditAction
from app.audit.logger import write_audit_entry
from app.core.logging import get_logger
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.orchestrator.executor import read_run_interrupt
from app.workers.bootstrap import WorkerDeps

log = get_logger("workers.reaper")

#: pending 重投计数键（带 TTL，防计数器泄漏）
_RETRY_KEY_TTL_SECONDS = 3600

#: 可被收敛的非终态状态
_REAPABLE_STATES = ("pending", "running")
_RUNNING_STATES = ("pending", "running", "paused")


@dataclass(slots=True)
class ReapSummary:
    """单次清扫结果计数（用于日志与交接证据）。"""

    scanned: int = 0
    paused: int = 0
    succeeded: int = 0
    cancelled: int = 0
    failed: int = 0
    redelivered: int = 0


async def reap_orphans(deps: WorkerDeps) -> ReapSummary:
    """扫描并收敛本 worker 可见的全部过期在途 run。"""
    summary = ReapSummary()
    grace = timedelta(seconds=deps.settings.run_orphan_grace_seconds)
    cutoff = datetime.now(tz=UTC) - grace

    async with deps.session_factory() as session:
        rows = await session.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.status.in_(_REAPABLE_STATES),
                ResearchRun.created_at < cutoff,
                or_(
                    ResearchRun.lease_until.is_(None),
                    ResearchRun.lease_until < cutoff,
                ),
            )
            .order_by(ResearchRun.created_at)
        )
        runs = list(rows.all())

    summary.scanned = len(runs)
    for run in runs:
        try:
            await _reap_one(deps, run, summary)
        except Exception as exc:  # noqa: BLE001 - 单 run 失败不阻断其余清扫
            log.exception("孤儿收敛单 run 失败，跳过", extra={"run_id": run.id, "error": repr(exc)})

    log.info(
        "孤儿清扫完成",
        extra={
            "scanned": summary.scanned,
            "paused": summary.paused,
            "succeeded": summary.succeeded,
            "cancelled": summary.cancelled,
            "failed": summary.failed,
            "redelivered": summary.redelivered,
        },
    )
    return summary


async def _reap_one(deps: WorkerDeps, run: ResearchRun, summary: ReapSummary) -> None:
    if str(run.status) == "pending" and run.started_at is None:
        await _reap_pending(deps, run, summary)
        return
    await _reap_running(deps, run, summary)


async def _reap_pending(deps: WorkerDeps, run: ResearchRun, summary: ReapSummary) -> None:
    """从未启动的 pending run：重投一次，超限置 failed。"""
    key = f"reap:retry:{run.id}"
    attempts = await deps.redis.incr(key)
    if attempts == 1:
        await deps.redis.expire(key, _RETRY_KEY_TTL_SECONDS)
        # 延迟导入：tasks.research 反向依赖 bootstrap，避免模块循环
        from app.workers.tasks.research import execute_run

        execute_run.delay(run.id)
        summary.redelivered += 1
        log.warning("pending run 首次清扫，重投 execute_run", extra={"run_id": run.id})
        return

    await _converge_failed(
        deps,
        run,
        error_code="RUN_WORKER_LOST",
        error_message="pending 任务重投后仍未启动，达到重投上限",
        summary=summary,
    )


async def _reap_running(deps: WorkerDeps, run: ResearchRun, summary: ReapSummary) -> None:
    """running 孤儿：checkpoint 挂起 → paused；有报告 → 按报告收敛；否则 failed。"""
    # 1) checkpoint 未消费 interrupt（worker 在无报告暂停帧前崩溃）
    interrupt = await read_run_interrupt(
        run_id=run.id,
        session_factory=deps.session_factory,
        checkpointer=deps.checkpointer,
        llm=deps.llm,
        retrieval_client=deps.retrieval_client,
    )
    if interrupt is not None and interrupt.get("reason"):
        converged = await _converge_status(deps, run, "paused")
        if converged:
            summary.paused += 1
            await _audit(deps, run, outcome="paused", reason="checkpoint_interrupt")
            await _publish(deps, run, "paused")
        return

    # 2) 以既有报告行为准收敛
    async with deps.session_factory() as session:
        report = await session.scalar(
            select(Report)
            .where(Report.run_id == run.id, Report.status.in_(("final", "draft")))
            .order_by(Report.created_at.desc())
        )
    if report is not None:
        target = "succeeded" if str(report.status) == "final" else "cancelled"
        converged = await _converge_status(deps, run, target)
        if converged:
            if target == "succeeded":
                summary.succeeded += 1
            else:
                summary.cancelled += 1
            await _audit(deps, run, outcome=target, reason=f"report_{report.status}")
            await _publish(
                deps,
                run,
                target,
                extra={"partial_report_id": report.id} if target == "cancelled" else None,
            )
        return

    # 3) 无挂起无报告：worker 真丢失
    await _converge_failed(
        deps,
        run,
        error_code="RUN_WORKER_LOST",
        error_message="执行租约过期且无检查点挂起/报告产出，判定 worker 丢失",
        summary=summary,
    )


async def _converge_status(deps: WorkerDeps, run: ResearchRun, status: str) -> bool:
    """条件更新 run 到终态/暂停态；已被并发收敛返回 False。"""
    now = datetime.now(tz=UTC)
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if status != "paused":
        values["finished_at"] = now
    async with deps.session_factory() as session:
        result = await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == run.id, ResearchRun.status.in_(_RUNNING_STATES))
            .values(**values)
        )
        if result.rowcount == 0:
            return False
        await session.commit()
    # 收敛后清理可能残留的 Redis 租约/控制键（非 owner，直接清键）
    await deps.redis.delete(f"lease:run:{run.id}", f"control:run:{run.id}")
    return True


async def _converge_failed(
    deps: WorkerDeps,
    run: ResearchRun,
    *,
    error_code: str,
    error_message: str,
    summary: ReapSummary,
) -> None:
    now = datetime.now(tz=UTC)
    async with deps.session_factory() as session:
        result = await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == run.id, ResearchRun.status.in_(_REAPABLE_STATES))
            .values(
                status="failed",
                finished_at=now,
                updated_at=now,
                error_code=error_code,
                error_message=error_message[:1000],
            )
        )
        if result.rowcount == 0:
            return
        await session.commit()
    await deps.redis.delete(f"lease:run:{run.id}", f"control:run:{run.id}")
    summary.failed += 1
    await _audit(deps, run, outcome="failed", reason=error_code)
    await _publish(deps, run, "failed", error_code=error_code, error_message=error_message[:1000])


async def _audit(deps: WorkerDeps, run: ResearchRun, *, outcome: str, reason: str) -> None:
    async with deps.session_factory() as session:
        await write_audit_entry(
            session,
            team_id="system",
            user_id="system",
            action=AuditAction.RUN_ORPHAN_RECOVERED,
            target_type="run",
            target_id=run.id,
            payload={"outcome": outcome, "reason": reason, "worker_id": deps.worker_id},
        )
        await session.commit()


async def _publish(
    deps: WorkerDeps,
    run: ResearchRun,
    status: str,
    *,
    extra: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """best-effort 补发终态帧（事件权威仍以 DB 为准，发失败仅告警）。"""
    payload: dict[str, Any] = {
        "type": "run.finished",
        "run_id": run.id,
        "status": status,
        "current_stage": run.current_stage,
        "token_used": int(run.token_used or 0),
    }
    if extra:
        payload.update(extra)
    if error_code is not None:
        payload["error_code"] = error_code
    if error_message is not None:
        payload["error_message"] = error_message
    try:
        await deps.hub.publish(f"runs:{run.id}", payload)
    except Exception as exc:  # noqa: BLE001 - 终态帧失败不影响收敛事实
        log.warning("孤儿收敛终态帧推送失败", extra={"run_id": run.id, "error": repr(exc)})


#: 进程级「启动已扫」标记（prefork 每子进程独立）
_startup_swept = False


async def sweep_once_at_startup(deps: WorkerDeps) -> ReapSummary | None:
    """worker 子进程首个任务执行前调用一次；关闭开关或已扫则跳过。"""
    global _startup_swept
    if not deps.settings.run_orphan_sweep_enabled or _startup_swept:
        return None
    _startup_swept = True
    return await reap_orphans(deps)


__all__ = [
    "ReapSummary",
    "reap_orphans",
    "sweep_once_at_startup",
]
