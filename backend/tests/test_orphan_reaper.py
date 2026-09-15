"""孤儿 run 清扫测试（M2-8b T5，B-AC-8）。

DB 侧以 AsyncMock 会话替身驱动（条件更新/报告查询），checkpointer 判定以
read_run_interrupt 替身给出；Redis 用 fakeredis（重投计数/键清理为真）。
扫描 SQL 的新鲜度过滤由 T6 真库集成覆盖。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import fakeredis.aioredis
import pytest

from app.core.config import get_settings
from app.db.models.run import ResearchRun
from app.orchestrator.lease import RunLease
from app.orchestrator.registry import RedisRunRegistry
from app.realtime.hub import RealtimeHub
from app.workers import reaper as reaper_module
from app.workers.bootstrap import WorkerDeps
from app.workers.tasks import research as research_tasks


@pytest.fixture(autouse=True)
def _reset_flag():
    reaper_module._startup_swept = False
    yield
    reaper_module._startup_swept = False


def _run(status: str, *, started: bool = True) -> ResearchRun:
    return ResearchRun(
        id="run-orphan-1",
        project_id="proj-1",
        creator_id="user-1",
        template_id="generic",
        tier="standard",
        question="测试问题",
        status=status,
        current_stage="retrieve",
        token_budget=1000,
        token_used=88,
        started_at=datetime.now(tz=UTC) - timedelta(minutes=5) if started else None,
        created_at=datetime.now(tz=UTC) - timedelta(minutes=5),
    )


async def _deps(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[ResearchRun],
    *,
    report: Any | None = None,
    interrupt: dict[str, Any] | None = None,
) -> WorkerDeps:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    lease = RunLease(client, worker_id="worker-reaper", ttl_seconds=30)
    hub = RealtimeHub(client)

    # 扫描/报告查询/条件更新全部用同一个 mock 会话
    scalars_result = MagicMock()
    scalars_result.all.return_value = runs
    session = AsyncMock()
    session.scalars = AsyncMock(return_value=scalars_result)
    session.scalar = AsyncMock(return_value=report)
    session.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)

    # 审计落库与检查点读取替身：本文件只验证 reaper 决策，不触真实 PG/图
    monkeypatch.setattr(reaper_module, "write_audit_entry", AsyncMock())
    monkeypatch.setattr(reaper_module, "read_run_interrupt", AsyncMock(return_value=interrupt))

    deps = WorkerDeps(
        settings=get_settings(),
        session_factory=factory,
        checkpointer=None,
        hub=hub,
        redis=client,
        lease=lease,
        registry=RedisRunRegistry(lease),
        worker_id="worker-reaper",
        llm=None,
        retrieval_client=None,
    )
    return deps


async def _frames(hub: RealtimeHub, run_id: str) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe(f"runs:{run_id}"):
            collected.append(event)
            if event.get("type") == "run.finished":
                break

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0.05)
    return collected, task


@pytest.mark.asyncio
async def test_interrupted_run_converges_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run("running")
    deps = await _deps(monkeypatch, [run], interrupt={"reason": "clarify"})
    events, collector = await _frames(deps.hub, run.id)

    summary = await reaper_module.reap_orphans(deps)
    await asyncio.wait_for(collector, timeout=2.0)

    assert summary.scanned == 1 and summary.paused == 1
    assert [e for e in events if e["type"] == "run.finished"][0]["status"] == "paused"
    reaper_module.write_audit_entry.assert_awaited_once()  # type: ignore[attr-defined]
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_final_report_converges_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run("running")
    report = SimpleNamespace(id="rep-1", status="final")
    deps = await _deps(monkeypatch, [run], report=report)
    events, collector = await _frames(deps.hub, run.id)

    summary = await reaper_module.reap_orphans(deps)
    await asyncio.wait_for(collector, timeout=2.0)

    assert summary.succeeded == 1
    frame = [e for e in events if e["type"] == "run.finished"][0]
    assert frame["status"] == "succeeded"
    assert "partial_report_id" not in frame
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_draft_report_converges_cancelled_with_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run("running")
    report = SimpleNamespace(id="rep-2", status="draft")
    deps = await _deps(monkeypatch, [run], report=report)
    events, collector = await _frames(deps.hub, run.id)

    summary = await reaper_module.reap_orphans(deps)
    await asyncio.wait_for(collector, timeout=2.0)

    assert summary.cancelled == 1
    frame = [e for e in events if e["type"] == "run.finished"][0]
    assert frame["status"] == "cancelled"
    assert frame["partial_report_id"] == "rep-2"
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_no_report_no_interrupt_converges_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run("running")
    deps = await _deps(monkeypatch, [run])
    events, collector = await _frames(deps.hub, run.id)

    summary = await reaper_module.reap_orphans(deps)
    await asyncio.wait_for(collector, timeout=2.0)

    assert summary.failed == 1
    frame = [e for e in events if e["type"] == "run.finished"][0]
    assert frame["status"] == "failed"
    assert frame["error_code"] == "RUN_WORKER_LOST"
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_pending_redelivered_once_then_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """pending 首次清扫重投；第二次清扫超过上限置 failed。"""
    delay = MagicMock()
    monkeypatch.setattr(research_tasks.execute_run, "delay", delay)

    # 第一次：重投
    run = _run("pending", started=False)
    deps = await _deps(monkeypatch, [run])
    summary = await reaper_module.reap_orphans(deps)
    assert summary.redelivered == 1 and summary.failed == 0
    delay.assert_called_once_with(run.id)
    assert int(await deps.redis.get(f"reap:retry:{run.id}")) == 1

    # 第二次：计数已为 1 → 直接 failed（同依赖，报告/挂起均无）
    events, collector = await _frames(deps.hub, run.id)
    summary = await reaper_module.reap_orphans(deps)
    await asyncio.wait_for(collector, timeout=2.0)
    assert summary.redelivered == 0 and summary.failed == 1
    frame = [e for e in events if e["type"] == "run.finished"][0]
    assert frame["status"] == "failed"
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_empty_scan_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = await _deps(monkeypatch, [])
    summary = await reaper_module.reap_orphans(deps)
    assert summary.scanned == 0
    await deps.hub.aclose()


@pytest.mark.asyncio
async def test_startup_sweep_respects_switch_and_once_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭时不扫；开启时每进程仅扫一次。"""
    run = _run("running")

    # 关闭：直接返回 None
    monkeypatch.setenv("RUN_ORPHAN_SWEEP_ENABLED", "false")
    get_settings.cache_clear()
    deps_off = await _deps(monkeypatch, [run])
    assert await reaper_module.sweep_once_at_startup(deps_off) is None
    assert reaper_module._startup_swept is False

    # 开启：首次执行，二次跳过
    monkeypatch.setenv("RUN_ORPHAN_SWEEP_ENABLED", "true")
    get_settings.cache_clear()
    deps_on = await _deps(monkeypatch, [run])
    first = await reaper_module.sweep_once_at_startup(deps_on)
    second = await reaper_module.sweep_once_at_startup(deps_on)
    assert first is not None and first.scanned == 1
    assert second is None
    await deps_off.hub.aclose()
    await deps_on.hub.aclose()
