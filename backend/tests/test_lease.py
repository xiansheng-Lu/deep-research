"""RunLease 租约/控制键测试（M2-8b，B-AC-1/2 键语义）。

Redis 用 fakeredis（decode_responses=True，与生产 build_redis 一致）；
TTL 过期用例用 1 秒短租约真实验证 PX 语义。
"""

from __future__ import annotations

import asyncio

import fakeredis
import fakeredis.aioredis
import pytest

from app.orchestrator.lease import RunLease


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_acquire_nx_mutual_exclusion(redis_client) -> None:
    """同 run 第二个执行者 NX 抢约失败，持有者信息可回读。"""
    worker_a = RunLease(redis_client, worker_id="worker-a", ttl_seconds=30)
    worker_b = RunLease(redis_client, worker_id="worker-b", ttl_seconds=30)

    assert await worker_a.try_acquire("run-1", "task-a") is True
    assert await worker_b.try_acquire("run-1", "task-b") is False

    holder = await worker_a.holder("run-1")
    assert holder is not None
    assert holder.worker_id == "worker-a"
    assert holder.task_id == "task-a"
    # 不同 run 互不影响
    assert await worker_b.try_acquire("run-2", "task-b") is True


@pytest.mark.asyncio
async def test_lease_expiry_allows_reacquire(redis_client) -> None:
    """TTL 过期后可被新执行者抢占（worker 崩溃后的重投语义）。"""
    old = RunLease(redis_client, worker_id="worker-a", ttl_seconds=1)
    new = RunLease(redis_client, worker_id="worker-b", ttl_seconds=1)
    assert await old.try_acquire("run-1", "task-a") is True
    await asyncio.sleep(1.15)
    assert await old.holder("run-1") is None
    assert await new.try_acquire("run-1", "task-b") is True


@pytest.mark.asyncio
async def test_heartbeat_renew_owner_checked(redis_client) -> None:
    """心跳续期：持有者成功；非持有者（含过期后被接管）失败。"""
    owner = RunLease(redis_client, worker_id="worker-a", ttl_seconds=30)
    other = RunLease(redis_client, worker_id="worker-b", ttl_seconds=30)
    assert await owner.try_acquire("run-1", "task-a") is True

    # task_id 不匹配（同 worker 的别的任务/旧任务）不得续期
    assert await owner.renew("run-1", "task-stale") is False
    # 别的 worker 不得续期
    assert await other.renew("run-1", "task-a") is False
    # 正主续期成功且租约仍在
    assert await owner.renew("run-1", "task-a") is True
    assert await other.holder("run-1") is not None

    # 过期被接管后，旧 owner 续期失败
    short = RunLease(redis_client, worker_id="worker-a", ttl_seconds=1)
    assert await short.try_acquire("run-2", "task-a") is True
    await asyncio.sleep(1.15)
    assert await short.renew("run-2", "task-a") is False


@pytest.mark.asyncio
async def test_release_owner_checked(redis_client) -> None:
    """释放：仅正主可 DEL；错误 owner 不动键；无键幂等成功语义为 False。"""
    owner = RunLease(redis_client, worker_id="worker-a", ttl_seconds=30)
    other = RunLease(redis_client, worker_id="worker-b", ttl_seconds=30)
    assert await owner.try_acquire("run-1", "task-a") is True

    assert await other.release("run-1", "task-a") is False
    assert await owner.holder("run-1") is not None
    assert await owner.release("run-1", "task-stale") is False
    assert await owner.holder("run-1") is not None
    assert await owner.release("run-1", "task-a") is True
    assert await owner.holder("run-1") is None
    # 已释放后重复释放返回 False（幂等不报错）
    assert await owner.release("run-1", "task-a") is False


class TestControlSignal:
    @pytest.mark.asyncio
    async def test_set_and_read(self, redis_client) -> None:
        lease = RunLease(redis_client, worker_id="w")
        assert await lease.read_control("run-1") is None
        assert await lease.set_control("run-1", "pause") is True
        signal = await lease.read_control("run-1")
        assert signal is not None
        assert signal.mode == "pause"
        assert signal.keep_partial is True

    @pytest.mark.asyncio
    async def test_upgrade_only_no_downgrade(self, redis_client) -> None:
        """pause->cancel 升级并带新 keep_partial；cancel->pause 拒绝降级。"""
        lease = RunLease(redis_client, worker_id="w")
        await lease.set_control("run-1", "pause")
        await lease.set_control("run-1", "cancel", keep_partial=False)
        signal = await lease.read_control("run-1")
        assert signal is not None
        assert signal.mode == "cancel"
        assert signal.keep_partial is False

        # 试图降级回 pause：信号保持 cancel
        assert await lease.set_control("run-1", "pause") is True
        signal = await lease.read_control("run-1")
        assert signal is not None
        assert signal.mode == "cancel"

    @pytest.mark.asyncio
    async def test_clear_on_terminal(self, redis_client) -> None:
        """终态清理控制键；重复清理静默。"""
        lease = RunLease(redis_client, worker_id="w")
        await lease.set_control("run-1", "pause")
        await lease.clear_control("run-1")
        assert await lease.read_control("run-1") is None
        await lease.clear_control("run-1")

    @pytest.mark.asyncio
    async def test_control_ttl_fallback(self, redis_client) -> None:
        """控制键带兜底 TTL，防终态清理遗漏造成键泄漏。"""
        lease = RunLease(redis_client, worker_id="w")
        await lease.set_control("run-1", "cancel")
        ttl = await redis_client.ttl("control:run:run-1")
        assert 0 < ttl <= 3600
