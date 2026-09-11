"""LLM 客户端门面：主备 Provider 路由、熔断、用量累加。

业务代码统一通过 ``LLMClient`` 调用，不直接持有 Provider 实例。
"""

import json
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


def _build_schema_instruction(schema: type[BaseModel]) -> str:
    """根据 Pydantic 模型生成 JSON 输出契约文本（随用户消息注入）。"""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        "\n\n输出要求：你必须仅返回一个符合以下 JSON Schema 的 JSON 对象，"
        "字段名严格一致，使用 UTF-8 中文文本作为字段值；"
        "不要输出 markdown 代码块、注释或任何 JSON 以外的文字。\n"
        f"JSON Schema：\n{schema_json}"
    )


def _inject_schema_instruction(
    messages: list[ChatMessage],
    schema: type[BaseModel],
) -> list[ChatMessage]:
    """把 JSON Schema 输出契约追加到最后一条用户消息。

    调用方消息恒含 user 消息（澄清问题 / 拆解载荷）；若无 user 消息则新建一条，
    保证 DeepSeek json_object 模式"消息内含 JSON 指令"的要求得到满足。
    """
    instruction = _build_schema_instruction(schema)
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].role == "user":
            anchored = list(messages)
            original = anchored[idx]
            anchored[idx] = ChatMessage(
                role="user",
                content=f"{original.content}{instruction}",
                name=original.name,
            )
            return anchored
    messages.append(ChatMessage(role="user", content=instruction.lstrip("\n")))
    return messages


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

        实现路径（兼容 OpenAI / DeepSeek 的 json_object 模式）：
        1. 把 Pydantic 生成的 JSON Schema 作为输出契约追加到用户消息，
           明确要求只输出一个 JSON 对象、不得包裹 markdown 代码块；
        2. 以 ``response_format={"type": "json_object"}`` 强制 JSON 输出
           （DeepSeek 不支持 OpenAI 的 strict json_schema，json_object 是双方
           共有能力，故统一走该模式）；
        3. ``json.loads`` 后用 ``schema.model_validate`` 校验。

        任何解析/校验失败都抛 ``ProviderUnavailableError``（§7.5 统一收敛）。
        """
        del tags  # M1 预留：链路标签暂不参与调用
        guided_messages = _inject_schema_instruction(list(messages), schema)
        request = ChatRequest(
            messages=guided_messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        chat_response = await self.chat(request)

        content = chat_response.content.strip()
        try:
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
