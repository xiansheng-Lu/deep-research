"""埋点接收进程内设施（M2-8a）：滑动窗口限流、稳定采样、ingest 计数。

单 uvicorn 副本语义：
- 限流/计数为进程内状态，多副本不精确（内部试用单副本）；M2-8b 引入 Redis
  后限流计数迁到共享存储，本模块届时替换实现而不改路由签名。
- 采样按 user_id 哈希稳定切流（同一用户在配置不变时结果恒定），不用每请求随机。
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any


class SlidingWindowRateLimiter:
    """每键 60 秒滑动窗口批次数限流（进程内、线程安全）。"""

    def __init__(self, max_per_minute: int, window_seconds: float = 60.0) -> None:
        self._max = max_per_minute
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """记录一次请求并返回是否放行；超限的请求不压入窗口。"""
        moment = time.monotonic() if now is None else now
        cutoff = moment - self._window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(moment)
            return True


def stable_sample(user_id: str, rate: float) -> bool:
    """按 user_id 稳定判定是否采样：rate=0 全丢、rate>=1 全收。

    同一 user_id 在 rate 不变时结果恒定（hash 落 [0,1) 区间）。
    """
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    digest = hashlib.blake2b(user_id.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big") / float(1 << 64)
    return value < rate


class IngestCounters:
    """进程内接收计数（重启归零，仅趋势观察，不参与业务正确性）。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self.accepted_events = 0
        self.dropped_events = 0
        self.rate_limited_batches = 0

    def add_accepted(self, count: int) -> None:
        with self._lock:
            self.accepted_events += count

    def add_dropped(self, count: int) -> None:
        with self._lock:
            self.dropped_events += count

    def add_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited_batches += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "accepted_events": self.accepted_events,
                "dropped_events": self.dropped_events,
                "rate_limited_batches": self.rate_limited_batches,
            }


#: 进程级单例
_rate_limiter: SlidingWindowRateLimiter | None = None
_counters = IngestCounters()


def get_rate_limiter(max_per_minute: int) -> SlidingWindowRateLimiter:
    """获取进程内限流器（首次按配置容量构造；配置变更不热重建，重启生效）。"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = SlidingWindowRateLimiter(max_per_minute)
    return _rate_limiter


def get_counters() -> IngestCounters:
    return _counters
