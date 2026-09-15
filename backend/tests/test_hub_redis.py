"""RealtimeHub 双态测试（M2-8b）：内存态不回归 + Redis 态跨进程扇出。

Redis 态用 fakeredis 的异步客户端 + 手动桥接两个 hub，模拟两个进程。
"""

from __future__ import annotations

import asyncio
import contextlib

import fakeredis
import fakeredis.aioredis
import pytest

from app.realtime.hub import RealtimeHub


class TestInMemoryHub:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_local_subscriber(self) -> None:
        hub = RealtimeHub()
        assert hub.is_redis_backed is False

        async def consume() -> dict:
            # 显式 aclose 订阅生成器，保证 finally 在客户端关闭前执行完
            agen = hub.subscribe("runs:r1")
            event = await agen.__anext__()
            await agen.aclose()
            return event

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await hub.publish("runs:r1", {"type": "x", "run_id": "r1"})
        event = await asyncio.wait_for(consumer, timeout=1)
        assert event == {"type": "x", "run_id": "r1"}
        await hub.aclose()

    @pytest.mark.asyncio
    async def test_channel_isolation(self) -> None:
        hub = RealtimeHub()
        got: list[dict] = []

        async def listen(channel: str) -> None:
            async for event in hub.subscribe(channel):
                got.append(event)

        task_a = asyncio.create_task(listen("runs:a"))
        await asyncio.sleep(0)
        await hub.publish("runs:b", {"run_id": "b"})
        await asyncio.sleep(0.05)
        assert got == []
        task_a.cancel()
        # 必须等到取消传播进生成器 finally 后再关 hub，避免悬挂的 Queue.get 协程
        with contextlib.suppress(asyncio.CancelledError):
            await task_a
        await hub.aclose()


class TestRedisHub:
    @pytest.mark.asyncio
    async def test_cross_process_fanout_and_no_loopback_duplicate(self) -> None:
        """worker hub 发布，API hub（另一 node）经 Redis 收到；发送方本地不重复。"""
        # 两个 hub 共用同一 fakeredis 服务端，模拟两个进程各自的客户端
        server = fakeredis.FakeServer()
        worker_redis = fakeredis.aioredis.FakeRedis(server=server)
        api_redis = fakeredis.aioredis.FakeRedis(server=server)

        worker_hub = RealtimeHub(worker_redis)
        api_hub = RealtimeHub(api_redis)
        await asyncio.sleep(0.05)  # 等桥接协程就绪并完成订阅握手

        api_events: list[dict] = []

        async def api_listen() -> None:
            async for event in api_hub.subscribe("runs:r1"):
                api_events.append(event)

        task = asyncio.create_task(api_listen())
        await asyncio.sleep(0.1)  # 等 SUBSCRIBE 生效

        # worker 侧无本地订阅者；事件经 Redis 到 api_hub
        await worker_hub.publish("runs:r1", {"type": "stage.ended", "run_id": "r1"})
        await asyncio.sleep(0.15)
        assert api_events == [{"type": "stage.ended", "run_id": "r1"}]

        # 收尾顺序：先让订阅生成器 finally（含 UNSUBSCRIBE）执行完，再关 hub/redis
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await worker_hub.aclose()
        await api_hub.aclose()
        await worker_redis.aclose()
        await api_redis.aclose()

    @pytest.mark.asyncio
    async def test_local_publish_does_not_require_redis_loopback(self) -> None:
        """同进程订阅者即使 Redis 桥接未收到回环，也由本地直投获得事件（不重复）。"""
        server = fakeredis.FakeServer()
        redis = fakeredis.aioredis.FakeRedis(server=server)
        hub = RealtimeHub(redis)
        await asyncio.sleep(0.05)

        events: list[dict] = []

        async def listen() -> None:
            async for event in hub.subscribe("runs:r2"):
                events.append(event)

        task = asyncio.create_task(listen())
        await asyncio.sleep(0.1)
        await hub.publish("runs:r2", {"run_id": "r2"})
        await asyncio.sleep(0.15)
        # 本地直投 1 次，自己 origin 的回环被桥接丢弃，不出现 2 次
        assert events == [{"run_id": "r2"}]

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await hub.aclose()
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_cross_process_channel_isolation(self) -> None:
        """pattern 订阅 runs:* 下，发给无本地订阅者频道的消息必须丢弃，不得串台。"""
        server = fakeredis.FakeServer()
        worker_redis = fakeredis.aioredis.FakeRedis(server=server)
        api_redis = fakeredis.aioredis.FakeRedis(server=server)

        worker_hub = RealtimeHub(worker_redis)
        api_hub = RealtimeHub(api_redis)
        await asyncio.sleep(0.05)

        r1_events: list[dict] = []

        async def api_listen() -> None:
            async for event in api_hub.subscribe("runs:r1"):
                r1_events.append(event)

        task = asyncio.create_task(api_listen())
        await asyncio.sleep(0.1)

        # r2 在 api 侧无订阅者：桥接收到 pmessage 后直接丢弃
        await worker_hub.publish("runs:r2", {"run_id": "r2"})
        await asyncio.sleep(0.1)
        # r1 正常送达
        await worker_hub.publish("runs:r1", {"run_id": "r1"})
        await asyncio.sleep(0.15)
        assert r1_events == [{"run_id": "r1"}]

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await worker_hub.aclose()
        await api_hub.aclose()
        await worker_redis.aclose()
        await api_redis.aclose()
