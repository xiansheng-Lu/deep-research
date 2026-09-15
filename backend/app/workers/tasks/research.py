"""research.execute_run / research.resume_run Celery 任务（M2-8b §14.2）。

同步任务体只做薄外壳：``asyncio.run`` 在 worker 进程事件循环内驱动既有
``run_research_async`` / ``resume_research_async``，业务执行器零改写
（不引入 gevent/eventlet）。

- 租约以 Celery task_id 为 owner；独立心跳协程每心跳间隔续期，不被业务
  await 阻塞（§16.2）；
- 执行器对瞬时 Provider 故障在 Redis 态下向上抛 ``ProviderUnavailableError``，
  由 Celery 有限重试（max_retries 配置）；预算耗尽在此落 failed + 终态帧
  （B-AC-7）；
- execute 参数全部从 run 行重载（重投递天然幂等，§16.4）；resume 的
  human_input 随任务载荷重放（aupdate_state 幂等）。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from celery.exceptions import MaxRetriesExceededError  # type: ignore[import-untyped]
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.db.models.project import Project
from app.db.models.run import ResearchRun
from app.orchestrator.executor import resume_research_async, run_research_async
from app.workers.bootstrap import WorkerDeps, worker_context
from app.workers.celery_app import celery_app
from app.workers.reaper import sweep_once_at_startup

log = get_logger("workers.research")

#: 重试退避间隔（秒）；内部试用固定短退避，抖动收益有限
_RETRY_COUNTDOWN_SECONDS = 2

#: 重试预算耗尽/崩溃收尾时允许写 failed 的非终态状态
_FAILED_STATES = ("pending", "running", "paused")


async def _load_run(deps: WorkerDeps, run_id: str) -> tuple[ResearchRun | None, str]:
    """加载 run 行与其 team_id（execute 任务参数全部以 DB 行为准）。"""
    async with deps.session_factory() as session:
        run = await session.get(ResearchRun, run_id)
        if run is None:
            return None, ""
        team_id = await session.scalar(select(Project.team_id).where(Project.id == run.project_id))
        return run, str(team_id or "")


async def _heartbeat(deps: WorkerDeps, run_id: str, task_id: str, stop: asyncio.Event) -> None:
    """独立心跳协程：周期性续 Redis 租约并把观测镜像写回 PG（§16.2）。

    业务长 await 不影响心跳；Redis 续期失败不杀业务（下轮自愈），PG 镜像写
    失败仅告警（Redis TTL 仍是权威）。
    """
    interval = deps.settings.run_lease_heartbeat_seconds
    while not stop.is_set():
        try:
            renewed = await deps.lease.renew(run_id, task_id)
            if renewed:
                await _mirror_lease_to_pg(deps, run_id, task_id)
            else:
                # 执行器尚未抢到（启动瞬间）或已释放（收尾中）：不告警，下轮再试
                log.debug("心跳本轮未续到租约", extra={"run_id": run_id, "task_id": task_id})
        except Exception as exc:  # noqa: BLE001 - 心跳失败不杀业务，下轮自愈
            log.warning("租约心跳续期异常", extra={"run_id": run_id, "err": repr(exc)})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def _mirror_lease_to_pg(deps: WorkerDeps, run_id: str, task_id: str) -> None:
    """把租约观测镜像（owner + 到期时间）低频写回 research_runs。"""
    lease_until = datetime.now(tz=UTC) + timedelta(seconds=deps.settings.run_lease_ttl_seconds)
    try:
        async with deps.session_factory() as session:
            await session.execute(
                update(ResearchRun)
                .where(ResearchRun.id == run_id)
                .values(execution_owner=task_id, lease_until=lease_until)
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 镜像失败不影响 Redis 权威租约
        log.warning("租约 PG 镜像写回失败", extra={"run_id": run_id, "error": repr(exc)})


async def _mark_terminal_failed(
    deps: WorkerDeps,
    run_id: str,
    *,
    error_code: str,
    error_message: str,
) -> None:
    """重试预算耗尽后独立收敛为 failed 并发终态帧（不再抛错，任务正常 ack）。"""
    now = datetime.now(tz=UTC)
    async with deps.session_factory() as session:
        result = await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == run_id, ResearchRun.status.in_(_FAILED_STATES))
            .values(
                status="failed",
                finished_at=now,
                updated_at=now,
                error_code=error_code,
                error_message=error_message[:1000],
            )
        )
        if result.rowcount == 0:
            # 已被并发自然终态/其他收敛路径处理：不覆盖、不重发
            log.info("终态写回未命中，跳过 failed 收敛", extra={"run_id": run_id})
            return
        await session.commit()

    await deps.hub.publish(
        f"runs:{run_id}",
        {
            "type": "run.finished",
            "run_id": run_id,
            "status": "failed",
            "error_code": error_code,
            "error_message": error_message[:1000],
        },
    )
    log.warning("重试预算耗尽，run 收敛为 failed", extra={"run_id": run_id, "error_code": error_code})


async def _execute(deps: WorkerDeps, celery_task: Any, run_id: str) -> None:
    """execute_run 的异步实体：加载 run、起心跳、驱动执行器并处理有限重试。"""
    await sweep_once_at_startup(deps)
    run, team_id = await _load_run(deps, run_id)
    if run is None:
        log.error("任务对应 run 不存在，ack 丢弃", extra={"run_id": run_id})
        return

    task_id = str(getattr(celery_task.request, "id", run_id))
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(deps, run_id, task_id, stop))
    try:
        await run_research_async(
            run_id=run.id,
            project_id=run.project_id,
            template_id=run.template_id,
            tier=run.tier,
            question=run.question,
            token_budget=run.token_budget,
            clarification=None,
            team_id=team_id,
            creator_id=run.creator_id,
            trace_id=task_id,
            session_factory=deps.session_factory,
            llm=deps.llm,
            retrieval_client=deps.retrieval_client,
            hub=deps.hub,
            checkpointer=deps.checkpointer,
            owner_id=task_id,
        )
    except ProviderUnavailableError as exc:
        await _handle_retry(deps, celery_task, run_id, exc)
    finally:
        stop.set()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


async def _resume(deps: WorkerDeps, celery_task: Any, run_id: str, human_input: dict[str, Any]) -> None:
    """resume_run 的异步实体：从检查点续跑，同样带心跳与有限重试。"""
    await sweep_once_at_startup(deps)
    task_id = str(getattr(celery_task.request, "id", run_id))
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(deps, run_id, task_id, stop))
    try:
        await resume_research_async(
            run_id=run_id,
            human_input=human_input,
            session_factory=deps.session_factory,
            checkpointer=deps.checkpointer,
            llm=deps.llm,
            retrieval_client=deps.retrieval_client,
            hub=deps.hub,
            owner_id=task_id,
        )
    except ProviderUnavailableError as exc:
        await _handle_retry(deps, celery_task, run_id, exc)
    finally:
        stop.set()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


async def _handle_retry(deps: WorkerDeps, celery_task: Any, run_id: str, exc: Exception) -> None:
    """瞬时 Provider 故障的有限重试；预算耗尽收敛 failed（B-AC-7）。"""
    try:
        celery_task.retry(exc=exc, countdown=_RETRY_COUNTDOWN_SECONDS)
    except MaxRetriesExceededError:
        await _mark_terminal_failed(
            deps,
            run_id,
            error_code="PROVIDER_UNAVAILABLE_RETRIES_EXHAUSTED",
            error_message=repr(exc),
        )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="research.execute_run", bind=True, max_retries=get_settings().celery_task_max_retries
)
def execute_run(celery_task: Any, run_id: str) -> None:
    """首次执行研究 run：worker 事件循环外壳。"""

    async def _main() -> None:
        async with worker_context() as deps:
            await _execute(deps, celery_task, run_id)

    asyncio.run(_main())


@celery_app.task(  # type: ignore[untyped-decorator]
    name="research.resume_run", bind=True, max_retries=get_settings().celery_task_max_retries
)
def resume_run(celery_task: Any, run_id: str, human_input: dict[str, Any] | None = None) -> None:
    """从暂停/挂起点恢复研究 run：worker 事件循环外壳。"""

    async def _main() -> None:
        async with worker_context() as deps:
            await _resume(deps, celery_task, run_id, human_input or {"kind": "proceed"})

    asyncio.run(_main())
