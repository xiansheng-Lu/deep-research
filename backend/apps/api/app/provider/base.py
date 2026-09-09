"""Provider 协议与数据契约。

任何实现 ``LLMProvider`` 的对象都能注册到 ``LLMClient`` 中，由后者负责
主备切换、超时、重试与熔断。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class ChatMessage:
    """对话消息。"""

    role: str  # system / user / assistant / tool
    content: str
    name: str | None = None


@dataclass(slots=True)
class ChatRequest:
    """Provider 调用入参。"""

    messages: list[ChatMessage]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatResponse:
    """Provider 调用出参。"""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Provider 协议：所有具体实现都应满足该签名。"""

    name: str

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""
        ...

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """同步式调用：返回完整响应。"""
        ...

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """流式调用：yield 增量文本片段。"""
        ...