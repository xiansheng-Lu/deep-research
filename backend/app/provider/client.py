"""LLM 客户端门面：主备 Provider 路由、熔断、用量累加。

业务代码统一通过 ``LLMClient`` 调用，不直接持有 Provider 实例。
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.exceptions import ProviderUnavailableError
from app.provider.base import ChatMessage, ChatRequest, ChatResponse, LLMProvider
from app.provider.circuit_breaker import CircuitBreaker
from app.provider.registry import default_registry
from app.provider.usage import UsageTracker

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class StructuredCompletion:
    """结构化输出结果包装。

    Attributes:
        parsed: 解析并校验后的 Pydantic 模型实例。
        usage: token 用量字典（与 ChatResponse.usage 同源）。
        model: 实际响应所用模型名。
        raw: 原始 JSON 内容（仅在解析失败时供诊断使用）。
    """

    parsed: BaseModel
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    raw: str = ""


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

    async def complete_structured(
        self,
        *,
        messages: Sequence[ChatMessage],
        schema: type[T],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tags: Sequence[str] | None = None,
    ) -> StructuredCompletion:
        """结构化输出：按 Pydantic 模型 ``schema`` 约束返回 JSON。

        实现路径：以 ``response_format={"type": "json_schema", ...}`` 引导 LLM 返回严格 JSON，
        解析后用 ``schema.model_validate`` 校验。任何解析/校验失败都会抛出
        ``ProviderUnavailableError``（§7.5 统一收敛）。M1 阶段不引入 Pydantic → JSON Schema
        的二次重写，直接采用 Pydantic v2 生成的 schema（已含 ``additionalProperties: false``）。
        """
        schema_dict = schema.model_json_schema()
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema_dict,
                "strict": True,
            },
        }
        request = ChatRequest(
            messages=list(messages),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        chat_response = await self.chat(request)

        content = chat_response.content.strip()
        try:
            import json

            payload = json.loads(content) if content else {}
            parsed = schema.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - 解析/校验统一收敛
            raise ProviderUnavailableError(
                f"结构化输出解析失败：{exc!r}（raw={content[:200]!r}）"
            ) from exc

        return StructuredCompletion(
            parsed=parsed,
            usage=chat_response.usage,
            model=chat_response.model,
            raw=content,
        )

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


__all__ = ["LLMClient", "StructuredCompletion"]
