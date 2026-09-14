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
