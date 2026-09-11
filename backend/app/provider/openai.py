"""OpenAI Provider 适配器：基于 langchain-openai v1 实现 LLMProvider 协议。

对齐 LLD §7.2；内部使用 ``ChatOpenAI`` 作为底层，对外暴露统一的
``chat/stream`` 接口，业务代码无需感知 LangChain 类型。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderUnavailableError
from app.provider.base import ChatMessage, ChatRequest, ChatResponse


def _to_langchain_message(msg: ChatMessage) -> SystemMessage | HumanMessage | AIMessage:
    """将自研 ``ChatMessage`` 转换为 LangChain 消息类型。"""
    if msg.role == "system":
        return SystemMessage(content=msg.content)
    if msg.role == "assistant":
        return AIMessage(content=msg.content)
    # user / tool 统一当 HumanMessage 处理（M1 阶段 tool 调用留到 WP-5）
    return HumanMessage(content=msg.content)


def _extract_usage(result: AIMessage) -> dict[str, int]:
    """从 AIMessage.usage_metadata 提取 token 用量。"""
    meta = getattr(result, "usage_metadata", None) or {}
    return {
        "prompt_tokens": int(meta.get("input_tokens", 0)),
        "completion_tokens": int(meta.get("output_tokens", 0)),
        "total_tokens": int(meta.get("total_tokens", 0)),
    }


class OpenAIProvider:
    """OpenAI Provider 适配器。

    通过 ``ChatOpenAI`` 调用 OpenAI API（或兼容的第三方网关）。
    """

    name = "openai"

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        """构造适配器。

        Args:
            model: 模型名称（如 gpt-4o / gpt-4o-mini）。
            api_key: OpenAI API Key；None 时从环境变量 OPENAI_API_KEY 读取。
            base_url: 自定义 API 端点（兼容第三方网关）。
            timeout: 单次调用超时（秒）。
        """
        self._model = model
        self._chat = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            temperature=0.7,
        )

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端（LangChain 无显式 close，此处为空操作）。"""
        # ChatOpenAI 内部使用 httpx，无显式 close；进程退出时自动释放
        return None

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """同步式调用：返回完整响应。"""
        try:
            msgs = [_to_langchain_message(m) for m in request.messages]
            started = time.monotonic()
            # 运行时参数通过 langchain 官方 bind 机制透传给底层 OpenAI 兼容网关
            # （DeepSeek 等第三方网关同样遵循该参数约定）
            invoke_kwargs: dict[str, Any] = {}
            if request.temperature is not None:
                invoke_kwargs["temperature"] = float(request.temperature)
            if request.max_tokens is not None:
                invoke_kwargs["max_tokens"] = int(request.max_tokens)
            if request.response_format is not None:
                invoke_kwargs["response_format"] = request.response_format
            runnable = self._chat.bind(**invoke_kwargs) if invoke_kwargs else self._chat
            result = await runnable.ainvoke(msgs)
            latency_ms = int((time.monotonic() - started) * 1000)

            if not isinstance(result, AIMessage):
                raise ProviderUnavailableError(f"OpenAI 返回非预期类型：{type(result).__name__}")

            content = getattr(result, "content", "") or ""
            usage = _extract_usage(result)
            tool_calls_raw = getattr(result, "tool_calls", None) or []
            tool_calls = [
                {"id": tc.get("id", ""), "name": tc.get("name", ""), "args": tc.get("args", {})}
                for tc in tool_calls_raw
            ]

            return ChatResponse(
                content=content,
                model=request.model or self._model,
                usage=usage,
                tool_calls=tool_calls,
                raw={"latency_ms": latency_ms, "finish_reason": "stop"},
            )
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"OpenAI 调用失败：{exc}") from exc

    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """流式调用：yield 增量文本片段。"""
        try:
            msgs = [_to_langchain_message(m) for m in request.messages]
            async for chunk in self._chat.astream(msgs):
                if isinstance(chunk, AIMessage) and chunk.content:
                    yield chunk.content
        except Exception as exc:
            raise ProviderUnavailableError(f"OpenAI 流式调用失败：{exc}") from exc


def _build_openai_provider(
    cfg: Settings,
    *,
    model: str,
    api_key_value: str,
    base_url: str,
) -> OpenAIProvider:
    """主/备 Provider 共用构造器（消除工厂重复）。"""
    return OpenAIProvider(
        model=model,
        api_key=api_key_value or None,
        base_url=base_url or None,
        timeout=float(cfg.llm_timeout_seconds),
    )


def build_openai_provider(settings: Settings | None = None) -> OpenAIProvider:
    """从配置构造 OpenAI Provider（主/备通用工厂）。"""
    cfg = settings or get_settings()
    return _build_openai_provider(
        cfg,
        model=cfg.llm_primary_model or "gpt-4o",
        api_key_value=cfg.llm_primary_api_key.get_secret_value(),
        base_url=cfg.llm_primary_base_url,
    )


def build_openai_backup_provider(settings: Settings | None = None) -> OpenAIProvider | None:
    """从配置构造备用 OpenAI Provider；若未配置则返回 None。"""
    cfg = settings or get_settings()
    if not cfg.llm_backup_model:
        return None
    return _build_openai_provider(
        cfg,
        model=cfg.llm_backup_model,
        api_key_value=cfg.llm_backup_api_key.get_secret_value(),
        base_url=cfg.llm_backup_base_url,
    )


_ = Any  # 防止未使用导入告警
