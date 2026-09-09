"""LLM 客户端门面：主备 Provider 路由、熔断、用量累加。

业务代码统一通过 ``LLMClient`` 调用，不直接持有 Provider 实例。
"""

from collections.abc import AsyncIterator
from typing import Any

from app.core.exceptions import ProviderUnavailableError
from app.provider.base import ChatRequest, ChatResponse, LLMProvider
from app.provider.circuit_breaker import CircuitBreaker
from app.provider.registry import default_registry
from app.provider.usage import UsageTracker


class LLMClient:
    """对外统一门面。"""

    def __init__(
        self,
        *,
        primary: LLMProvider | None = None,
        backup: LLMProvider | None = None,
        breaker: CircuitBreaker | None = None,
        usage: UsageTracker | None = None,
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._breaker = breaker or CircuitBreaker()
        self._usage = usage or UsageTracker()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """同步调用：先主后备；熔断开启时直接走备用。"""
        provider = self._select_provider()
        try:
            response = await provider.chat(request)
        except ProviderUnavailableError:
            response = await self._fallback(request)
        self._usage.record(provider.name, response.usage)
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """流式调用：先尝试主 Provider，失败则降级到备用。"""
        provider = self._select_provider()
        try:
            async for chunk in provider.stream(request):
                yield chunk
        except ProviderUnavailableError:
            async for chunk in self._backup.stream(request):
                yield chunk

    def _select_provider(self) -> LLMProvider:
        """根据熔断状态选择 Provider。"""
        if self._primary is None:
            if self._backup is None:
                raise ProviderUnavailableError("未注册任何 LLM Provider")
            return self._backup
        if self._breaker.is_open(self._primary.name):
            if self._backup is None:
                raise ProviderUnavailableError(f"主 Provider {self._primary.name} 已熔断，且无备用 Provider")
            return self._backup
        return self._primary

    async def _fallback(self, request: ChatRequest) -> ChatResponse:
        """主 Provider 失败时的备用调用。"""
        if self._backup is None:
            raise
        self._breaker.trip(self._primary.name if self._primary else "primary")
        response = await self._backup.chat(request)
        self._usage.record(self._backup.name, response.usage)
        return response

    @classmethod
    def from_registry(cls, *, name: str = "default") -> "LLMClient":
        """从全局注册表构造客户端。"""
        primary, backup = default_registry.build_pair(name)
        return cls(primary=primary, backup=backup)


_default_client: LLMClient | None = None


def get_default_client() -> LLMClient:
    """惰性构造默认客户端。"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient.from_registry()
    return _default_client


_ = Any  # 防止未使用导入告警；M1 阶段使用