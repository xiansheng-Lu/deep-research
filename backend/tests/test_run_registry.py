"""``RunRegistry`` 在途任务注册表单元测试（M2-5）。"""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.registry import RunRegistry


async def _idle() -> None:
    """挂住直到被取消的在途协程。"""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        # 吞掉取消正常退出（与执行器受控收尾同构）
        return


@pytest.mark.asyncio
async def test_register_is_active_unregister() -> None:
    registry = RunRegistry()
    assert registry.is_active("run-1") is False

    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        registry.register("run-1", task)
        assert registry.is_active("run-1") is True

        registry.unregister("run-1")
        assert registry.is_active("run-1") is False
        # 重复注销不报错
        registry.unregister("run-1")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_stop_pause_delivers_cancel_and_keeps_mode() -> None:
    registry = RunRegistry()
    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        registry.register("run-1", task)
        signalled = registry.request_stop("run-1", "pause", keep_partial=False)
        assert signalled is True
        assert registry.consume_mode("run-1") == "pause"
        assert registry.keep_partial_for("run-1") is False
        # 任务确实收到取消信号
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()
    finally:
        registry.unregister("run-1")


@pytest.mark.asyncio
async def test_request_stop_without_handle_returns_false() -> None:
    registry = RunRegistry()
    assert registry.request_stop("missing", "cancel") is False
    assert registry.consume_mode("missing") is None
    # keep_partial 缺省为 True
    assert registry.keep_partial_for("missing") is True


@pytest.mark.asyncio
async def test_stop_mode_upgrades_pause_to_cancel_but_never_downgrades() -> None:
    registry = RunRegistry()
    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        registry.register("run-1", task)
        assert registry.request_stop("run-1", "pause") is True
        # 升级
        assert registry.request_stop("run-1", "cancel", keep_partial=False) is True
        assert registry.consume_mode("run-1") == "cancel"
        assert registry.keep_partial_for("run-1") is False
        # 降级被忽略
        assert registry.request_stop("run-1", "pause", keep_partial=True) is True
        assert registry.consume_mode("run-1") == "cancel"
        assert registry.keep_partial_for("run-1") is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        registry.unregister("run-1")


@pytest.mark.asyncio
async def test_done_task_is_not_active_and_stop_returns_false() -> None:
    registry = RunRegistry()

    async def _finish() -> None:
        return None

    task = asyncio.create_task(_finish())
    await asyncio.sleep(0)
    registry.register("run-1", task)
    assert task.done()
    assert registry.is_active("run-1") is False
    assert registry.request_stop("run-1", "pause") is False


# ---------------------------------------------------------------------------
# M2-5 修复（交接单 §9.1）：恢复预留 / 占位接管 / 属主感知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_places_placeholder_and_is_active() -> None:
    registry = RunRegistry()
    assert registry.reserve("run-1") is None
    assert registry.is_reserved("run-1") is True
    # 占位视为在途（pause/cancel 可落在占位上）
    assert registry.is_active("run-1") is True
    registry.unregister("run-1")
    assert registry.is_active("run-1") is False


@pytest.mark.asyncio
async def test_register_adopts_reservation_and_carries_stop_mode() -> None:
    registry = RunRegistry()
    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        # 恢复服务先占位，pause 信号落在占位（无任务可 cancel，仅记录）
        assert registry.reserve("run-1") is None
        assert registry.request_stop("run-1", "pause") is True
        # 恢复协程注册：接管占位并沿用暂停信号
        prior = registry.register("run-1", task)
        assert prior is not None and prior.task is None
        assert registry.peek_mode("run-1") == "pause"
        assert registry.is_owner("run-1", task) is True
        await asyncio.gather(task, return_exceptions=True)
    finally:
        registry.unregister("run-1", owner=task)
        assert registry.is_active("run-1") is False


@pytest.mark.asyncio
async def test_duplicate_reserve_restored_and_rejected() -> None:
    """已有恢复占位/在途恢复协程时重复抢占：旧句柄可放回，占位不变。"""
    registry = RunRegistry()
    # 第一次抢占：占位
    first_prior = registry.reserve("run-1")
    assert first_prior is None
    # 第二次抢占拿到占位；模拟服务层拒绝后 restore
    second_prior = registry.reserve("run-1")
    assert second_prior is not None and second_prior.task is None
    registry.restore("run-1", second_prior)
    assert registry.is_reserved("run-1") is True
    registry.unregister("run-1")


@pytest.mark.asyncio
async def test_unregister_owner_aware_keeps_new_handle() -> None:
    """被取代的旧协程注销时不能弹掉新恢复协程的句柄。"""
    registry = RunRegistry()
    old_task = asyncio.create_task(_idle())
    new_task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        registry.register("run-1", old_task)
        registry.register("run-1", new_task)
        # 旧协程退出：属主不匹配，不弹出新句柄
        registry.unregister("run-1", owner=old_task)
        assert registry.is_owner("run-1", new_task) is True
        assert registry.is_active("run-1") is True
        # 新协程正常注销
        registry.unregister("run-1", owner=new_task)
        assert registry.is_active("run-1") is False
    finally:
        for t in (old_task, new_task):
            t.cancel()
        await asyncio.gather(old_task, new_task, return_exceptions=True)
