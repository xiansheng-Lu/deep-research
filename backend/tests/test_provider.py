"""Provider 适配器 + LLMClient 门面 + 用量/熔断 单测。

不连真实 OpenAI API；通过 ``unittest.mock`` 替换 ``ChatOpenAI.ainvoke/astream``。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.core.exceptions import ProviderUnavailableError
from app.orchestrator.schemas import SubQuestionListSchema
from app.provider.base import ChatMessage, ChatRequest, ChatResponse, LLMProvider
from app.provider.circuit_breaker import CircuitBreaker
from app.provider.client import LLMClient
from app.provider.openai import OpenAIProvider, _extract_usage, _to_langchain_message
from app.provider.usage import UsageTracker


# ====== 辅助函数 ======


def _make_ai_message(
    content: str = "hello",
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
    tool_calls: list[dict[str, Any]] | None = None,
) -> AIMessage:
    """构造一个真实的 LangChain AIMessage（含 usage_metadata）。"""
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        response_metadata={"finish_reason": "stop"},
        # LangChain v1 的 usage_metadata 作为额外属性
        **{"usage_metadata": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }},
    )


def _make_request(**kwargs: Any) -> ChatRequest:
    """构造最小 ChatRequest。"""
    defaults: dict[str, Any] = {
        "messages": [ChatMessage(role="user", content="hello")],
    }
    defaults.update(kwargs)
    return ChatRequest(**defaults)


# ====== _to_langchain_message ======


class TestToLangchainMessage:
    def test_system_message(self) -> None:
        msg = _to_langchain_message(ChatMessage(role="system", content="sys"))
        assert msg.content == "sys"
        assert type(msg).__name__ == "SystemMessage"

    def test_user_message(self) -> None:
        msg = _to_langchain_message(ChatMessage(role="user", content="usr"))
        assert msg.content == "usr"
        assert type(msg).__name__ == "HumanMessage"

    def test_assistant_message(self) -> None:
        msg = _to_langchain_message(ChatMessage(role="assistant", content="ast"))
        assert msg.content == "ast"
        assert type(msg).__name__ == "AIMessage"

    def test_tool_message_falls_back_to_human(self) -> None:
        msg = _to_langchain_message(ChatMessage(role="tool", content="result"))
        assert type(msg).__name__ == "HumanMessage"


# ====== _extract_usage ======


class TestExtractUsage:
    def test_normal_usage(self) -> None:
        ai = _make_ai_message(input_tokens=20, output_tokens=30)
        usage = _extract_usage(ai)
        assert usage["prompt_tokens"] == 20
        assert usage["completion_tokens"] == 30
        assert usage["total_tokens"] == 50

    def test_missing_usage_metadata(self) -> None:
        ai = MagicMock(spec=[])  # 无 usage_metadata 属性
        usage = _extract_usage(ai)
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ====== OpenAIProvider.chat ======


class TestOpenAIProviderChatAsync:
    """异步 chat 测试。"""

    async def test_chat_success(self) -> None:
        provider = OpenAIProvider(model="gpt-4o", api_key="fake")
        fake_msg = _make_ai_message("hi", input_tokens=10, output_tokens=5)

        # 直接替换 _chat 为 mock
        mock_chat = MagicMock()
        mock_chat.ainvoke = AsyncMock(return_value=fake_msg)
        provider._chat = mock_chat

        resp = await provider.chat(_make_request())

        assert resp.content == "hi"
        assert resp.model == "gpt-4o"
        assert resp.usage["total_tokens"] == 15
        mock_chat.ainvoke.assert_called_once()

    async def test_chat_raises_on_exception(self) -> None:
        provider = OpenAIProvider(model="gpt-4o", api_key="fake")

        mock_chat = MagicMock()
        mock_chat.ainvoke = AsyncMock(side_effect=RuntimeError("net"))
        provider._chat = mock_chat

        with pytest.raises(ProviderUnavailableError, match="OpenAI 调用失败"):
            await provider.chat(_make_request())


# ====== OpenAIProvider.stream ======


class TestOpenAIProviderStream:
    async def test_stream_yields_chunks(self) -> None:
        provider = OpenAIProvider(model="gpt-4o", api_key="fake")
        chunk1 = _make_ai_message("hel")
        chunk2 = _make_ai_message("lo")

        async def _fake_stream(*args: Any, **kwargs: Any):
            yield chunk1
            yield chunk2

        mock_chat = MagicMock()
        mock_chat.astream = _fake_stream
        provider._chat = mock_chat

        chunks = [chunk async for chunk in provider.stream(_make_request())]

        assert chunks == ["hel", "lo"]

    async def test_stream_raises_on_error(self) -> None:
        provider = OpenAIProvider(model="gpt-4o", api_key="fake")

        async def _fail(*args: Any, **kwargs: Any):
            raise RuntimeError("net")
            yield  # type: ignore  # noqa: unreachable

        mock_chat = MagicMock()
        mock_chat.astream = _fail
        provider._chat = mock_chat

        with pytest.raises(ProviderUnavailableError, match="流式调用失败"):
            _ = [c async for c in provider.stream(_make_request())]


# ====== UsageTracker ======


class TestUsageTracker:
    def test_record_and_snapshot(self) -> None:
        tracker = UsageTracker()
        tracker.record("openai", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        tracker.record("openai", {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})

        snap = tracker.snapshot()
        assert "openai" in snap
        assert snap["openai"].prompt_tokens == 30
        assert snap["openai"].completion_tokens == 15
        assert snap["openai"].total_tokens == 45
        assert snap["openai"].calls == 2

    def test_empty_usage_ignored(self) -> None:
        tracker = UsageTracker()
        tracker.record("openai", {})
        assert tracker.snapshot() == {}

    def test_reset_clears(self) -> None:
        tracker = UsageTracker()
        tracker.record("openai", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        tracker.reset()
        assert tracker.snapshot() == {}


# ====== CircuitBreaker ======


class TestCircuitBreaker:
    def test_open_after_threshold(self) -> None:
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        assert cb.is_open("p") is False
        cb.record_failure("p")
        cb.record_failure("p")
        assert cb.is_open("p") is False
        cb.record_failure("p")
        assert cb.is_open("p") is True

    def test_success_resets_count(self) -> None:
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        cb.record_failure("p")
        cb.record_failure("p")
        cb.record_success("p")
        assert cb.is_open("p") is False

    def test_trip_force_opens(self) -> None:
        cb = CircuitBreaker(fail_threshold=10, reset_seconds=60)
        cb.trip("p")
        assert cb.is_open("p") is True


# ====== LLMClient ======


class TestLLMClient:
    def _mock_provider(self, name: str = "mock") -> MagicMock:
        p = MagicMock(spec=LLMProvider)
        p.name = name
        return p

    async def test_chat_delegates_to_primary(self) -> None:
        primary = self._mock_provider("primary")
        primary.chat = AsyncMock(return_value=ChatResponse(content="ok", model="m"))
        client = LLMClient(primary=primary)

        resp = await client.chat(_make_request())
        assert resp.content == "ok"
        primary.chat.assert_called_once()

    async def test_fallback_on_provider_error(self) -> None:
        primary = self._mock_provider("primary")
        primary.chat = AsyncMock(side_effect=ProviderUnavailableError("down"))
        backup = self._mock_provider("backup")
        backup.chat = AsyncMock(return_value=ChatResponse(content="fallback", model="m"))
        client = LLMClient(primary=primary, backup=backup)

        resp = await client.chat(_make_request())
        assert resp.content == "fallback"
        backup.chat.assert_called_once()

    async def test_no_providers_raises(self) -> None:
        client = LLMClient()
        with pytest.raises(ProviderUnavailableError, match="未注册"):
            await client.chat(_make_request())

    async def test_usage_recorded(self) -> None:
        primary = self._mock_provider("primary")
        primary.chat = AsyncMock(
            return_value=ChatResponse(content="ok", model="m", usage={"total_tokens": 100})
        )
        tracker = UsageTracker()
        client = LLMClient(primary=primary, usage=tracker)

        await client.chat(_make_request())
        snap = tracker.snapshot()
        assert snap["primary"].total_tokens == 100


# ====== Provider Protocol 检查 ======


class TestOpenAIProviderProtocol:
    def test_implements_protocol(self) -> None:
        """OpenAIProvider 应满足 LLMProvider Protocol。"""
        provider = OpenAIProvider(model="gpt-4o", api_key="fake")
        assert isinstance(provider, LLMProvider)


# ====== complete_structured：json_object 模式（DeepSeek 兼容） ======


class TestCompleteStructured:
    def _make_client(self, content: str) -> tuple[LLMClient, MagicMock]:
        primary = MagicMock(spec=LLMProvider)
        primary.name = "primary"
        primary.chat = AsyncMock(
            return_value=ChatResponse(
                content=content,
                model="deepseek-v4-flash",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        )
        return LLMClient(primary=primary), primary

    async def test_json_object_mode_and_schema_injection(self) -> None:
        parsed_json = '{"sub_questions": [{"question": "子问题1", "depends_on": [], "rationale": ""}]}'
        client, primary = self._make_client(parsed_json)

        completion = await client.complete_structured(
            messages=[
                ChatMessage(role="system", content="你是拆解助手"),
                ChatMessage(role="user", content="拆解：AI 趋势"),
            ],
            schema=SubQuestionListSchema,
        )

        # 响应模型解析正确，usage 透传
        assert isinstance(completion.parsed, SubQuestionListSchema)
        assert completion.parsed.sub_questions[0].question == "子问题1"
        assert completion.usage["total_tokens"] == 15
        assert completion.model == "deepseek-v4-flash"

        sent_request: ChatRequest = primary.chat.call_args[0][0]
        # DeepSeek 兼容路径：json_object 而非 strict json_schema
        assert sent_request.response_format == {"type": "json_object"}
        # JSON Schema 契约注入到最后一条 user 消息
        last_user = [m for m in sent_request.messages if m.role == "user"][-1]
        assert "JSON Schema" in last_user.content
        assert "拆解：AI 趋势" in last_user.content

    async def test_extra_fields_echoed_by_model_are_ignored(self) -> None:
        """DeepSeek 在 json_object 模式下会回显 {"type": "json_object"}，
        入站 Schema 必须忽略多余字段而非整体校验失败。"""
        from app.orchestrator.schemas import ClarificationSchema

        parsed_json = (
            '{"type": "json_object", "requires_user_input": false, '
            '"questions": [], "defaults": {}, "structured_question": {"goal": "g"}}'
        )
        client, _ = self._make_client(parsed_json)
        completion = await client.complete_structured(
            messages=[ChatMessage(role="user", content="x")],
            schema=ClarificationSchema,
        )
        assert completion.parsed.requires_user_input is False
        assert completion.parsed.structured_question == {"goal": "g"}

    async def test_invalid_json_raises_provider_error(self) -> None:
        client, _ = self._make_client("这不是 JSON")
        with pytest.raises(ProviderUnavailableError, match="结构化输出解析失败"):
            await client.complete_structured(
                messages=[ChatMessage(role="user", content="x")],
                schema=SubQuestionListSchema,
            )

    async def test_schema_validation_failure_raises(self) -> None:
        # 缺必填 question 字段
        client, _ = self._make_client('{"sub_questions": [{"depends_on": []}]}')
        with pytest.raises(ProviderUnavailableError):
            await client.complete_structured(
                messages=[ChatMessage(role="user", content="x")],
                schema=SubQuestionListSchema,
            )

    async def test_instruction_injected_when_no_user_message(self) -> None:
        """无 user 消息时新建一条承载 schema 指令（满足 json_object 提示要求）。"""
        parsed_json = '{"sub_questions": []}'
        client, primary = self._make_client(parsed_json)
        await client.complete_structured(
            messages=[ChatMessage(role="system", content="sys")],
            schema=SubQuestionListSchema,
        )
        sent_request: ChatRequest = primary.chat.call_args[0][0]
        assert any(m.role == "user" and "JSON Schema" in m.content for m in sent_request.messages)
