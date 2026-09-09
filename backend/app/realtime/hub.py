"""实时事件总线占位。

跨进程广播通过 Redis Pub/Sub 转发；M1 阶段实现 connection 管理与心跳。
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.core.logging import get_logger

log = get_logger("realtime.hub")


class RealtimeHub:
    """实时事件总线占位。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """向指定 channel 投递事件。"""
        log.debug("hub publish", extra={"channel": channel})
        for queue in self._subscribers.get(channel, []):
            await queue.put(event)

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        """订阅指定 channel 的事件流。"""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(channel, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[channel].remove(queue)


_default_hub: RealtimeHub | None = None


def get_hub() -> RealtimeHub:
    """获取默认 Hub 实例。"""
    global _default_hub
    if _default_hub is None:
        _default_hub = RealtimeHub()
    return _default_hub