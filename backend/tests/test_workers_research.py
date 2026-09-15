"""Celery research 任务外壳与 worker 装配测试（M2-8b T4，B-AC-6/7）。

不连真实 Redis/Celery worker/Postgres：fakeredis 替身注入 worker_context，
executor 以 AsyncMock 替身验证 owner_id 透传、心跳、有限重试与终态收敛。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import fakeredis.aioredis
import pytest
from celery.exceptions import MaxRetriesExceededError, Retry

from app.core.config import get_settings
from app.core.exceptions import ProviderUnavailableError
from app.db.models.run import ResearchRun
from app.orchestrator.lease import RunLease
from app.orchestrator.registry import (
    RedisRunRegistry,
    get_run_registry,
    reset_run_registry,
)
from app.realtime.hub import RealtimeHub, set_default_hub
from app.workers import bootstrap as bootstrap_module
from app.workers import reaper as reaper_module
from app.workers.bootstrap import WorkerDeps, worker_context
from app.workers.tasks import research as research_tasks


@pytest.fixture(autouse=True)
def _disable_startup_sweep(monkeypatch: pytest.MonkeyPatch):
    """任务外壳测试不触发启动清扫（reaper 行为由 test_orphan_reaper 专测）。"""
    monkeypatch.setenv("RUN_ORPHAN_SWEEP_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(reaper_module, "_startup_swept", False)
    yield
    monkeypatch.setattr(reaper_module, "_startup_swept", False)


@pytest.fixture
async def fake_redis(monkeypatch: pytest.MonkeyPatch):
    """把 worker_context 的 Redis 构造替换为 fakeredis。"""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(bootstrap_module, "build_redis", lambda settings: client)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_worker_context_requires_redis_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """WORKER_ENABLE_REDIS=false 时 worker 快速失败，不允许静默内存态。"""
    monkeypatch.setenv("WORKER_ENABLE_REDIS", "false")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="WORKER_ENABLE_REDIS"):
        async with worker_context():
            pass


@pytest.mark.asyncio
async def test_worker_context_wires_redis_components(fake_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """true + Redis 可达：hub/租约/注册表全部 Redis 态并完成注入与清理。"""
    monkeypatch.setenv("WORKER_ENABLE_REDIS", "true")
    get_settings.cache_clear()
    try:
        async with worker_context() as deps:
            assert isinstance(deps, WorkerDeps)
            assert deps.registry.is_redis_backed is True
            assert get_run_registry() is deps.registry
            assert deps.hub.is_redis_backed is True
            assert await deps.redis.ping() is True
            # 桥接协程已订阅 runs:*
            await deps.redis.publish("runs:probe", '{"origin":"x"}')
            await asyncio.sleep(0.05)
    finally:
        reset_run_registry()
        set_default_hub(RealtimeHub())
        get_settings.cache_clear()


def _run_row() -> ResearchRun:
    return ResearchRun(
        id="run-w-1",
        project_id="proj-1",
        creator_id="user-1",
        template_id="generic",
        tier="standard",
        question="测试问题",
        status="running",
        current_stage="retrieve",
        token_budget=1000,
        token_used=10,
        started_at=datetime.now(tz=UTC),
    )


async def _deps_for_task(fake_redis, run: ResearchRun | None = None) -> WorkerDeps:
    """构造任务外壳用依赖（executor/DB 均以 mock 替身，fakeredis 为真）。"""
    lease = RunLease(fake_redis, worker_id="worker-1", ttl_seconds=30)
    registry = RedisRunRegistry(lease)
    hub = RealtimeHub(fake_redis)

    session = AsyncMock()
    session.get = AsyncMock(return_value=run)
    session.scalar = AsyncMock(return_value="team-1")

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)

    return WorkerDeps(
        settings=get_settings(),
        session_factory=factory,
        checkpointer=None,
        hub=hub,
        redis=fake_redis,
        lease=lease,
        registry=registry,
        worker_id="worker-1",
        llm=None,
        retrieval_client=None,
    )


def _celery_task_stub(task_id: str, retry_side_effect: Any | None = None) -> Any:
    retry = MagicMock()
    if retry_side_effect is not None:
        retry.side_effect = retry_side_effect
    return SimpleNamespace(request=SimpleNamespace(id=task_id), retry=retry)


@pytest.mark.asyncio
async def test_execute_passes_celery_owner_and_drives(fake_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """execute 外壳以 Celery task_id 为 owner 驱动执行器，并实际持有租约。"""
    run = _run_row()
    deps = await _deps_for_task(fake_redis, run)
    driven: dict[str, Any] = {}

    async def _fake_run_research_async(**kwargs: Any) -> None:
        driven.update(kwargs)
        # 模拟执行器生命周期：抢约（owner 必须等于 celery task id）后收尾释放
        await deps.registry.register(kwargs["run_id"], kwargs["owner_id"])
        await deps.registry.clear_stop_signal(kwargs["run_id"])
        await deps.registry.unregister(kwargs["run_id"], owner=kwargs["owner_id"])

    monkeypatch.setattr(research_tasks, "run_research_async", _fake_run_research_async)
    task = _celery_task_stub("celery-exec-1")

    await research_tasks._execute(deps, task, run.id)

    assert driven["owner_id"] == "celery-exec-1"
    assert driven["run_id"] == run.id
    assert driven["hub"] is deps.hub
    # 收尾后租约已释放（执行器 finally unregister）
    assert await deps.registry.is_owner(run.id, "celery-exec-1") is False
    task.retry.assert_not_called()
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_execute_missing_run_acks_without_drive(fake_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """run 行不存在：ack 返回，不调度执行器。"""
    deps = await _deps_for_task(fake_redis, None)
    run_mock = AsyncMock()
    monkeypatch.setattr(research_tasks, "run_research_async", run_mock)

    await research_tasks._execute(deps, _celery_task_stub("celery-x"), "missing")
    run_mock.assert_not_called()
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_retry_scheduled_when_provider_unavailable(fake_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """瞬时故障且仍有重试预算：self.retry 抛出 Retry，任务不做终态收敛。"""
    run = _run_row()
    deps = await _deps_for_task(fake_redis, run)
    monkeypatch.setattr(
        research_tasks,
        "run_research_async",
        AsyncMock(side_effect=ProviderUnavailableError("provider down")),
    )
    task = _celery_task_stub("celery-retry-1", retry_side_effect=Retry("scheduled"))

    with pytest.raises(Retry):
        await research_tasks._execute(deps, task, run.id)
    task.retry.assert_called_once()
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_retry_budget_exhausted_terminates_failed(fake_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """重试预算耗尽：独立写 failed 并发恰好一条 run.finished(failed)（B-AC-7）。"""
    run = _run_row()
    deps = await _deps_for_task(fake_redis, run)
    monkeypatch.setattr(
        research_tasks,
        "run_research_async",
        AsyncMock(side_effect=ProviderUnavailableError("provider down")),
    )
    task = _celery_task_stub("celery-retry-2", retry_side_effect=MaxRetriesExceededError())

    events: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in deps.hub.subscribe(f"runs:{run.id}"):
            events.append(event)
            if event.get("type") == "run.finished":
                break

    collector = asyncio.create_task(_collect())
    await asyncio.sleep(0.05)
    await research_tasks._execute(deps, task, run.id)
    await asyncio.wait_for(collector, timeout=2.0)

    assert len(events) == 1
    assert events[0]["type"] == "run.finished"
    assert events[0]["status"] == "failed"
    assert events[0]["error_code"] == "PROVIDER_UNAVAILABLE_RETRIES_EXHAUSTED"
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_resume_passes_human_input_and_owner(fake_redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """resume 外壳透传 human_input 与 Celery owner。"""
    run = _run_row()
    deps = await _deps_for_task(fake_redis, run)
    captured: dict[str, Any] = {}

    async def _fake_resume(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(research_tasks, "resume_research_async", _fake_resume)
    payload = {"answers": {"scope": "近三年"}}
    await research_tasks._resume(deps, _celery_task_stub("celery-resume-1"), run.id, payload)

    assert captured["owner_id"] == "celery-resume-1"
    assert captured["human_input"] == payload
    await deps.hub.aclose()
