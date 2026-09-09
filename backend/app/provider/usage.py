"""用量计量与成本累加。

记录每个 Provider 的输入/输出 token 数与单次调用的成本预估，
汇总后写回 ResearchRun 与 Quota 表。
"""

from dataclasses import dataclass, field
from threading import Lock


@dataclass(slots=True)
class UsageBucket:
    """单个 Provider 的累计用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


class UsageTracker:
    """进程内用量聚合占位。"""

    def __init__(self) -> None:
        self._buckets: dict[str, UsageBucket] = {}
        self._lock = Lock()

    def record(self, provider_name: str, usage: dict[str, int]) -> None:
        """累加一次调用产生的用量。"""
        if not usage:
            return
        with self._lock:
            bucket = self._buckets.setdefault(provider_name, UsageBucket())
            bucket.prompt_tokens += int(usage.get("prompt_tokens", 0))
            bucket.completion_tokens += int(usage.get("completion_tokens", 0))
            bucket.total_tokens += int(usage.get("total_tokens", 0))
            bucket.calls += 1

    def snapshot(self) -> dict[str, UsageBucket]:
        """读取当前各 Provider 的累计用量（浅拷贝）。"""
        with self._lock:
            return dict(self._buckets)

    def reset(self) -> None:
        """清空所有计数（用于测试）。"""
        with self._lock:
            self._buckets.clear()

    def _unused(self) -> None:  # pragma: no cover - 保留字段访问
        _ = field(default_factory=UsageBucket)