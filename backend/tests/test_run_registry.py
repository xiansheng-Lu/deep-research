"""在途任务注册表双态单元测试（M2-5 内存态 / M2-8b Redis 态）。"""

from __future__ import annotations

import asyncio

import fakeredis
import fakeredis.aioredis
import pytest

from app.orchestrator.lease import RunLease
from app.orchestrator.registry import RedisRunRegistry, RunRegistry


async def _idle() -> None:
    """挂住直到被取消的在途协程。"""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        # 吞掉取消正常退出（与执行器受控收尾同构）
        return


# ---------------------------------------------------------------------------
# 内存态 RunRegistry（M2-5 语义原样保留）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_is_active_unregister() -> None:
    registry = RunRegistry()
    assert await registry.is_active("run-1") is False

    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        await registry.register("run-1", task)
        assert await registry.is_active("run-1") is True

        await registry.unregister("run-1")
        assert await registry.is_active("run-1") is False
        # 重复注销不报错
        await registry.unregister("run-1")
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
        await registry.register("run-1", task)
        signalled = await registry.request_stop("run-1", "pause", keep_partial=False)
        assert signalled is True
        assert await registry.consume_mode("run-1") == "pause"
        assert await registry.keep_partial_for("run-1") is False
        # 任务确实收到取消信号
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()
    finally:
        await registry.unregister("run-1")


@pytest.mark.asyncio
async def test_request_stop_without_handle_returns_false() -> None:
    registry = RunRegistry()
    assert await registry.request_stop("missing", "cancel") is False
    assert await registry.consume_mode("missing") is None
    # keep_partial 缺省为 True
    assert await registry.keep_partial_for("missing") is True


@pytest.mark.asyncio
async def test_stop_mode_upgrades_pause_to_cancel_but_never_downgrades() -> None:
    registry = RunRegistry()
    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        await registry.register("run-1", task)
        assert await registry.request_stop("run-1", "pause") is True
        # 升级
        assert await registry.request_stop("run-1", "cancel", keep_partial=False) is True
        assert await registry.consume_mode("run-1") == "cancel"
        assert await registry.keep_partial_for("run-1") is False
        # 降级被忽略
        assert await registry.request_stop("run-1", "pause", keep_partial=True) is True
        assert await registry.consume_mode("run-1") == "cancel"
        assert await registry.keep_partial_for("run-1") is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await registry.unregister("run-1")


@pytest.mark.asyncio
async def test_done_task_is_not_active_and_stop_returns_false() -> None:
    registry = RunRegistry()

    async def _finish() -> None:
        return None

    task = asyncio.create_task(_finish())
    await asyncio.sleep(0)
    await registry.register("run-1", task)
    assert task.done()
    assert await registry.is_active("run-1") is False
    assert await registry.request_stop("run-1", "pause") is False


# ---------------------------------------------------------------------------
# M2-5 修复（交接单 §9.1）：恢复预留 / 占位接管 / 属主感知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_places_placeholder_and_is_active() -> None:
    registry = RunRegistry()
    assert await registry.reserve("run-1") is None
    assert await registry.is_reserved("run-1") is True
    # 占位视为在途（pause/cancel 可落在占位上）
    assert await registry.is_active("run-1") is True
    await registry.unregister("run-1")
    assert await registry.is_active("run-1") is False


@pytest.mark.asyncio
async def test_register_adopts_reservation_and_carries_stop_mode() -> None:
    registry = RunRegistry()
    task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        # 恢复服务先占位，pause 信号落在占位（无任务可 cancel，仅记录）
        assert await registry.reserve("run-1") is None
        assert await registry.request_stop("run-1", "pause") is True
        # 恢复协程注册：接管占位并沿用暂停信号
        prior = await registry.register("run-1", task)
        assert prior is not None and prior.task is None
        assert await registry.peek_mode("run-1") == "pause"
        assert await registry.is_owner("run-1", task) is True
        await asyncio.gather(task, return_exceptions=True)
    finally:
        await registry.unregister("run-1", owner=task)
        assert await registry.is_active("run-1") is False


@pytest.mark.asyncio
async def test_duplicate_reserve_restored_and_rejected() -> None:
    """已有恢复占位/在途恢复协程时重复抢占：旧句柄可放回，占位不变。"""
    registry = RunRegistry()
    # 第一次抢占：占位
    first_prior = await registry.reserve("run-1")
    assert first_prior is None
    # 第二次抢占拿到占位；模拟服务层拒绝后 restore
    second_prior = await registry.reserve("run-1")
    assert second_prior is not None and second_prior.task is None
    await registry.restore("run-1", second_prior)
    assert await registry.is_reserved("run-1") is True
    await registry.unregister("run-1")


@pytest.mark.asyncio
async def test_unregister_owner_aware_keeps_new_handle() -> None:
    """被取代的旧协程注销时不能弹掉新恢复协程的句柄。"""
    registry = RunRegistry()
    old_task = asyncio.create_task(_idle())
    new_task = asyncio.create_task(_idle())
    await asyncio.sleep(0)
    try:
        await registry.register("run-1", old_task)
        await registry.register("run-1", new_task)
        # 旧协程退出：属主不匹配，不弹出新句柄
        await registry.unregister("run-1", owner=old_task)
        assert await registry.is_owner("run-1", new_task) is True
        assert await registry.is_active("run-1") is True
        # 新协程正常注销
        await registry.unregister("run-1", owner=new_task)
        assert await registry.is_active("run-1") is False
    finally:
        for t in (old_task, new_task):
            t.cancel()
        await asyncio.gather(old_task, new_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Redis 态 RedisRunRegistry（M2-8b 租约/控制键适配）
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_registry():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    lease = RunLease(client, worker_id="worker-1", ttl_seconds=30)
    registry = RedisRunRegistry(lease)
    yield registry
    await client.aclose()


class TestRedisRunRegistry:
    @pytest.mark.asyncio
    async def test_register_nx_and_duplicate_marker(self, redis_registry) -> None:
        """首次抢约成功返回 None；重复投递拿到「他人持有」标记。"""
        assert await redis_registry.register("run-1", "task-a") is None
        prior = await redis_registry.register("run-1", "task-b")
        assert prior is not None and prior.task is None
        assert await redis_registry.is_active("run-1") is True
        # 属主判定精确到本 worker + task_id
        assert await redis_registry.is_owner("run-1", "task-a") is True
        assert await redis_registry.is_owner("run-1", "task-b") is False

    @pytest.mark.asyncio
    async def test_unregister_releases_only_owner(self, redis_registry) -> None:
        await redis_registry.register("run-1", "task-a")
        # 别的任务标识释放无效
        await redis_registry.unregister("run-1", owner="task-b")
        assert await redis_registry.is_active("run-1") is True
        # 无 owner 不允许无条件删除（防误清新执行者租约）
        await redis_registry.unregister("run-1", owner=None)
        assert await redis_registry.is_active("run-1") is True
        # 正主释放
        await redis_registry.unregister("run-1", owner="task-a")
        assert await redis_registry.is_active("run-1") is False

    @pytest.mark.asyncio
    async def test_request_stop_requires_fresh_lease(self, redis_registry) -> None:
        """无新鲜租约 request_stop 返回 False（调用方走无执行者收尾）。"""
        assert await redis_registry.request_stop("run-1", "pause") is False
        await redis_registry.register("run-1", "task-a")
        assert await redis_registry.request_stop("run-1", "pause") is True
        assert await redis_registry.peek_mode("run-1") == "pause"
        assert await redis_registry.consume_mode("run-1") == "pause"
        assert await redis_registry.keep_partial_for("run-1") is True

    @pytest.mark.asyncio
    async def test_cancel_keeps_partial_flag(self, redis_registry) -> None:
        await redis_registry.register("run-1", "task-a")
        assert await redis_registry.request_stop("run-1", "cancel", keep_partial=False) is True
        assert await redis_registry.consume_mode("run-1") == "cancel"
        assert await redis_registry.keep_partial_for("run-1") is False
        # pause 不允许降级
        assert await redis_registry.request_stop("run-1", "pause") is True
        assert await redis_registry.consume_mode("run-1") == "cancel"

    @pytest.mark.asyncio
    async def test_reservation_is_retired(self, redis_registry) -> None:
        """Redis 态恢复占位退役：reserve/restore/is_reserved 均为空操作。"""
        assert await redis_registry.reserve("run-1") is None
        assert await redis_registry.is_reserved("run-1") is False
        assert await redis_registry.is_active("run-1") is False
        await redis_registry.restore("run-1", None)
        # 无控制键时模式读取为空
        assert await redis_registry.peek_mode("run-1") is None
