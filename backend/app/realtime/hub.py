"""实时事件总线（M2-8b 双态：进程内队列 / Redis Pub-Sub 扇出）。

- **内存态**（``redis_client=None``，离线测试/单进程）：publish 直接投本进程
  订阅队列，行为同 M2-8b 之前。
- **Redis 态**（worker 与多 API 副本）：每个进程一个 node_id；后台桥接协程启动即
  ``PSUBSCRIBE runs:*``（频道语义恒为 ``runs:{run_id}``）。publish 先直投本地
  订阅者，再 PUBLISH 到 Redis（信封携带 origin node_id）；桥接收到 ``pmessage``
  后丢弃本节点回环，把其他节点的事件投到本地订阅队列。同进程订阅者不依赖 Redis
  回环（本地直投已覆盖），跨进程经桥接覆盖，不重复不遗漏。

WS 处理器与各节点发布代码零改动：channel 语义 ``runs:{id}`` 不变。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger("realtime.hub")

#: Redis 态桥接协程统一订阅的频道模式（run 频道命名恒为 runs:{run_id}）
RUN_CHANNEL_PATTERN = "runs:*"


class RealtimeHub:
    """事件总线：内存直连 + 可选 Redis Pub/Sub 桥接。"""

    def __init__(self, redis_client: Redis | None = None) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._redis: Redis | None = redis_client
        # 进程内唯一节点标识：回环消息据此去重
        self.node_id = uuid.uuid4().hex
        self._pubsub = self._redis.pubsub() if self._redis is not None else None
        self._bridge_task: asyncio.Task[None] | None = None
        if self._redis is not None:
            # 建连即占住模式订阅：listen() 仅在 subscribed 时循环，
            # 必须先 psubscribe 再进入 listen，否则桥接会静默退出
            self._bridge_task = asyncio.create_task(self._redis_bridge())

    @property
    def is_redis_backed(self) -> bool:
        return self._redis is not None

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """向指定频道投递事件：本地订阅者必投；Redis 态再扇出到其他节点。"""
        log.debug("hub publish", extra={"channel": channel})
        # 快照遍历：put 是 await 点，订阅者可能恰在同一轮收尾改表
        for queue in list(self._subscribers.get(channel, [])):
            await queue.put(event)
        if self._redis is not None:
            envelope = {"origin": self.node_id, "channel": channel, "event": event}
            try:
                await self._redis.publish(channel, json.dumps(envelope, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001 - 事件扇出失败不阻塞业务落库
                log.warning("Redis 事件扇出失败，本地订阅已投", extra={"channel": channel, "err": repr(exc)})

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        """订阅指定频道的事件流（本地队列；跨进程消息由桥接协程写入）。"""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(channel, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            # 防御：hub 收尾/并发取消后频道桶可能已被摘除
            bucket = self._subscribers.get(channel)
            if bucket is not None and queue in bucket:
                bucket.remove(queue)
                if not bucket:
                    self._subscribers.pop(channel, None)

    async def _redis_bridge(self) -> None:
        """PSUBSCRIBE runs:* 并把其他节点的事件投递到本地订阅者。"""
        assert self._pubsub is not None
        pubsub = self._pubsub
        try:
            await pubsub.psubscribe(RUN_CHANNEL_PATTERN)
            async for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    # psubscribe 确认帧等非业务消息忽略
                    continue
                try:
                    raw_data = message["data"]
                    envelope = json.loads(raw_data)
                except (KeyError, ValueError, TypeError):
                    continue
                # 自己发出的回环丢弃（本地订阅已在 publish 时直投）
                if envelope.get("origin") == self.node_id:
                    continue
                channel = envelope.get("channel")
                event = envelope.get("event")
                # fakeredis 未配 decode_responses 时 channel 可能是 bytes
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")
                if not isinstance(channel, str) or not isinstance(event, dict):
                    continue
                for queue in list(self._subscribers.get(channel, [])):
                    await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis 桥接订阅异常退出", extra={"err": repr(exc)})

    async def aclose(self) -> None:
        """关闭桥接协程与 pubsub（进程退出时调用；不关闭注入的 Redis 客户端本身）。"""
        if self._bridge_task is not None:
            self._bridge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._bridge_task
            self._bridge_task = None
        if self._pubsub is not None:
            # aclose 断开底层连接，服务端模式订阅随之清除（无需再发 PUNSUBSCRIBE）
            # redis-py 5.3 PubSub.aclose 缺返回注解，库侧类型缺口显式忽略
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()  # type: ignore[no-untyped-call]
            self._pubsub = None


_default_hub: RealtimeHub | None = None


def get_hub() -> RealtimeHub:
    """获取默认 Hub 实例（未显式初始化时为内存态）。"""
    global _default_hub
    if _default_hub is None:
        _default_hub = RealtimeHub()
    return _default_hub


def set_default_hub(hub: RealtimeHub) -> None:
    """设置进程级默认 Hub（lifespan/worker bootstrap 注入双态实例）。"""
    global _default_hub
    _default_hub = hub
