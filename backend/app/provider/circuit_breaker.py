"""熔断器：超过阈值后熔断一段时间，期间直接走备用 Provider。"""

from threading import Lock
from time import monotonic


class CircuitBreaker:
    """进程内熔断器（占位实现）。

    M1 阶段会替换为基于 Redis 的分布式版本，以支持多副本场景。
    """

    def __init__(self, *, fail_threshold: int = 5, reset_seconds: int = 60) -> None:
        self._fail_threshold = fail_threshold
        self._reset_seconds = reset_seconds
        self._state: dict[str, _BreakerState] = {}
        self._lock = Lock()

    def is_open(self, key: str) -> bool:
        """返回是否处于熔断状态。"""
        with self._lock:
            state = self._state.get(key)
            if state is None:
                return False
            if state.open_until and state.open_until < monotonic():
                # 冷却结束：尝试半开
                state.failures = 0
                state.open_until = None
                return False
            return state.open_until is not None

    def record_success(self, key: str) -> None:
        """调用成功：清零失败计数。"""
        with self._lock:
            state = self._state.get(key)
            if state is None:
                return
            state.failures = 0

    def record_failure(self, key: str) -> None:
        """调用失败：累计失败次数，达到阈值则熔断。"""
        with self._lock:
            state = self._state.setdefault(key, _BreakerState())
            state.failures += 1
            if state.failures >= self._fail_threshold:
                state.open_until = monotonic() + self._reset_seconds

    def trip(self, key: str) -> None:
        """强制熔断（主 Provider 异常时由 client 调用）。"""
        with self._lock:
            self._state.setdefault(key, _BreakerState()).open_until = (
                monotonic() + self._reset_seconds
            )


class _BreakerState:
    __slots__ = ("failures", "open_until")

    def __init__(self) -> None:
        self.failures = 0
        self.open_until: float | None = None