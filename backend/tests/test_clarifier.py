"""WP-5.2 clarifier 节点单元测试：覆盖 LLM 命中、降级、已澄清、空问题四种路径。

依赖 LLM 时通过 ``FakeLLMClient`` 注入，避免真实网络调用；不依赖 LLM 时直接走降级路径。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes import clarifier
from app.orchestrator.schemas import ClarificationQuestion, ClarificationSchema
from app.provider.base import ChatMessage, ChatResponse, LLMProvider
from app.provider.client import LLMClient

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class _FakeLLMProvider:
    """满足 LLMProvider 协议的极简替身。"""

    name = "fake"

    def __init__(self, parsed: ClarificationSchema | None = None) -> None:
        self._parsed = parsed
        self.last_messages: list[ChatMessage] = []

    async def aclose(self) -> None:  # noqa: D401
        return None

    async def chat(self, request: Any) -> ChatResponse:
        from app.provider.base import ChatRequest

        assert isinstance(request, ChatRequest)
        self.last_messages = list(request.messages)
        return ChatResponse(
            content=self._parsed.model_dump_json() if self._parsed else "{}",
            model="fake-model",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

    async def stream(self, request: Any):  # noqa: ARG002
        if False:
            yield ""


class _FakeStructuredLLMClient(LLMClient):
    """绕开真实 provider.chat()，直接在 complete_structured 内返回预设结果。"""

    def __init__(self, parsed: ClarificationSchema) -> None:
        # 不调用 super().__init__ 以避免依赖熔断器/注册表
        self._parsed = parsed

    async def chat(self, request: Any) -> ChatResponse:  # noqa: D401
        return ChatResponse(
            content=self._parsed.model_dump_json(),
            model="fake-model",
            usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        )


# ---------------------------------------------------------------------------
# 已澄清场景
# ---------------------------------------------------------------------------


class TestClarifierSkipped:
    @pytest.mark.asyncio
    async def test_already_clarified_returns_skipped(self) -> None:
        state: dict[str, Any] = {
            "question": "AI 的最新进展",
            "clarification": {"goal": "AI 进展", "scope": "2025", "_source": "user"},
        }
        result = await clarifier.run(state)
        assert result["needs_clarification"] is False
        # 不应设置 interrupt_reason
        assert "interrupt_reason" not in result

    @pytest.mark.asyncio
    async def test_already_clarified_with_no_deps(self) -> None:
        state: dict[str, Any] = {
            "question": "X",
            "clarification": {"goal": "X"},
        }
        result = await clarifier.run(state, deps=None)
        assert result["needs_clarification"] is False


# ---------------------------------------------------------------------------
# 空问题场景
# ---------------------------------------------------------------------------


class TestClarifierEmptyQuestion:
    @pytest.mark.asyncio
    async def test_empty_question_requests_user_input(self) -> None:
        state: dict[str, Any] = {"question": ""}
        result = await clarifier.run(state)
        assert result["needs_clarification"] is True
        assert result["interrupt_reason"] == "clarify"
        assert "questions" in result["interrupt_payload"]
        assert result["interrupt_payload"]["questions"][0]["key"] == "topic"

    @pytest.mark.asyncio
    async def test_whitespace_question_requests_user_input(self) -> None:
        state: dict[str, Any] = {"question": "   "}
        result = await clarifier.run(state)
        assert result["needs_clarification"] is True


# ---------------------------------------------------------------------------
# LLM 命中场景
# ---------------------------------------------------------------------------


class TestClarifierWithLLM:
    @pytest.mark.asyncio
    async def test_llm_requires_user_input(self) -> None:
        schema = ClarificationSchema(
            requires_user_input=True,
            questions=[
                ClarificationQuestion(
                    key="timeframe",
                    text="请选择时间范围",
                    options=["近一年", "近三年", "近五年"],
                    recommended=1,
                ),
                ClarificationQuestion(
                    key="region",
                    text="请选择地理范围",
                    options=["全球", "中国", "美国"],
                    recommended=None,
                ),
            ],
            defaults={},
            structured_question={},
        )
        client = _FakeStructuredLLMClient(schema)
        deps = NodeDeps(run_id="r1", team_id="t1", trace_id="tr1", llm=client)
        state: dict[str, Any] = {"question": "AI 最新进展"}

        result = await clarifier.run(state, deps=deps)

        assert result["needs_clarification"] is True
        assert result["interrupt_reason"] == "clarify"
        payload = result["interrupt_payload"]
        assert len(payload["questions"]) == 2
        assert payload["questions"][0]["key"] == "timeframe"
        assert payload["questions"][0]["recommended"] == 1
        assert payload["defaults"] == {}

    @pytest.mark.asyncio
    async def test_llm_no_clarification_needed(self) -> None:
        schema = ClarificationSchema(
            requires_user_input=False,
            questions=[],
            defaults={"scope": "近一年"},
            structured_question={
                "goal": "梳理 LLM 在医疗影像的落地",
                "scope": "近一年",
                "key_concepts": ["LLM", "医疗影像"],
                "constraints": ["中文"],
            },
        )
        client = _FakeStructuredLLMClient(schema)
        deps = NodeDeps(run_id="r2", team_id="t2", trace_id="tr2", llm=client)
        state: dict[str, Any] = {"question": "梳理 LLM 在医疗影像的近一年落地"}

        result = await clarifier.run(state, deps=deps)

        assert result["needs_clarification"] is False
        clarification = result["clarification"]
        assert clarification["goal"].startswith("梳理 LLM")
        assert clarification["scope"] == "近一年"
        assert clarification["key_concepts"] == ["LLM", "医疗影像"]
        assert clarification["_source"] == "clarifier.llm"
        # defaults 与 structured_question 已合并
        assert clarification["scope"] == "近一年"

    @pytest.mark.asyncio
    async def test_llm_call_failure_falls_back(self) -> None:
        """LLM 抛异常时：捕获并走降级。"""

        class _BoomClient(LLMClient):
            async def chat(self, request: Any) -> ChatResponse:  # noqa: D401
                raise RuntimeError("openai 502")

        deps = NodeDeps(run_id="r3", team_id="t3", trace_id="tr3", llm=_BoomClient())
        state: dict[str, Any] = {"question": "请研究 AI 安全治理框架及政策影响"}
        result = await clarifier.run(state, deps=deps)
        # 长度 > 8，启发式判定不需要追问
        assert result["needs_clarification"] is False
        assert result["clarification"]["_source"] == "clarifier.fallback"
        assert result["clarification"]["goal"] == state["question"]

    @pytest.mark.asyncio
    async def test_llm_parsed_type_mismatch_falls_back(self) -> None:
        """LLM 返回与 schema 不匹配的对象：降级。"""

        class _BadSchemaClient(LLMClient):
            async def chat(self, request: Any) -> ChatResponse:  # noqa: D401
                return ChatResponse(
                    content='{"not_a_valid_field": true}',
                    model="fake",
                    usage={},
                )

        deps = NodeDeps(run_id="r4", team_id="t4", trace_id="tr4", llm=_BadSchemaClient())
        state: dict[str, Any] = {"question": "请研究 AI 法规"}
        result = await clarifier.run(state, deps=deps)
        assert result["needs_clarification"] is False
        assert result["clarification"]["_source"] == "clarifier.fallback"


# ---------------------------------------------------------------------------
# 降级路径
# ---------------------------------------------------------------------------


class TestClarifierFallback:
    @pytest.mark.asyncio
    async def test_no_deps_short_question_requires_input(self) -> None:
        """无 deps + 短问题 → 降级到追问。"""
        state: dict[str, Any] = {"question": "AI"}
        result = await clarifier.run(state, deps=None)
        assert result["needs_clarification"] is True
        assert result["interrupt_reason"] == "clarify"
        assert result["interrupt_payload"]["questions"][0]["key"] == "scope"

    @pytest.mark.asyncio
    async def test_no_deps_long_question_passes(self) -> None:
        state: dict[str, Any] = {"question": "请梳理 2024-2025 年间 LLM 在医疗影像的最新落地"}
        result = await clarifier.run(state, deps=None)
        assert result["needs_clarification"] is False
        assert result["clarification"]["_source"] == "clarifier.fallback"

    @pytest.mark.asyncio
    async def test_deps_without_llm_treated_as_no_llm(self) -> None:
        deps = NodeDeps(run_id="r5", team_id="t5", trace_id="tr5", llm=None)
        state: dict[str, Any] = {"question": "请梳理 AI 行业趋势"}
        result = await clarifier.run(state, deps=deps)
        assert result["needs_clarification"] is False
        assert result["clarification"]["_source"] == "clarifier.fallback"


# ---------------------------------------------------------------------------
# 状态字段约定（§6.2 / §6.3）
# ---------------------------------------------------------------------------


class TestClarifierStateContract:
    @pytest.mark.asyncio
    async def test_interrupt_payload_shape(self) -> None:
        schema = ClarificationSchema(
            requires_user_input=True,
            questions=[
                ClarificationQuestion(key="x", text="t", options=["a"], recommended=0),
            ],
            defaults={"k": "v"},
            structured_question={},
        )
        client = _FakeStructuredLLMClient(schema)
        deps = NodeDeps(run_id="r6", team_id="t6", trace_id="tr6", llm=client)
        state: dict[str, Any] = {"question": "?"}
        result = await clarifier.run(state, deps=deps)

        payload = result["interrupt_payload"]
        assert set(payload.keys()) == {"questions", "defaults"}
        assert isinstance(payload["questions"], list)
        q = payload["questions"][0]
        assert set(q.keys()) == {"key", "text", "options", "recommended"}

    @pytest.mark.asyncio
    async def test_clarification_merges_defaults_and_structured(self) -> None:
        schema = ClarificationSchema(
            requires_user_input=False,
            questions=[],
            defaults={"a": 1, "shared": "from_defaults"},
            structured_question={"b": 2, "shared": "from_structured"},
        )
        client = _FakeStructuredLLMClient(schema)
        deps = NodeDeps(run_id="r7", team_id="t7", trace_id="tr7", llm=client)
        state: dict[str, Any] = {"question": "ok"}
        result = await clarifier.run(state, deps=deps)
        clarification = result["clarification"]
        # structured_question 后写入 → 同名键覆盖 defaults
        assert clarification["a"] == 1
        assert clarification["b"] == 2
        assert clarification["shared"] == "from_structured"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


class TestClarificationToStatePayload:
    def test_normal_answers(self) -> None:
        payload = {"answers": {"scope": "近一年", "region": "中国"}}
        result = clarifier.clarification_to_state_payload(payload)
        assert "clarification" in result
        assert result["clarification"]["_human_answers"] == payload["answers"]

    def test_empty_payload(self) -> None:
        assert clarifier.clarification_to_state_payload(None) == {}
        assert clarifier.clarification_to_state_payload({}) == {}


# ---------------------------------------------------------------------------
# token 用量累加（成本闸门准则：真实 LLM 调用必须回写 state.token_used）
# ---------------------------------------------------------------------------


class TestClarifierTokenAccounting:
    @pytest.mark.asyncio
    async def test_llm_hit_accumulates_token_used(self) -> None:
        """LLM 命中路径：本次 usage.total 累加进 state 原有 token_used。"""
        schema = ClarificationSchema(
            requires_user_input=False,
            questions=[],
            defaults={},
            structured_question={"goal": "目标", "scope": "范围"},
        )
        client = _FakeStructuredLLMClient(schema)  # total_tokens=12
        deps = NodeDeps(run_id="r8", team_id="t8", trace_id="tr8", llm=client)
        state: dict[str, Any] = {
            "question": "请研究 2026 年国产大模型的商业化进展",
            "token_used": 100,
        }

        result = await clarifier.run(state, deps=deps)

        assert result["token_used"] == 112

    @pytest.mark.asyncio
    async def test_fallback_path_does_not_inflate_token_used(self) -> None:
        """降级路径未产生真实调用，不得凭空增加 token_used。"""
        state: dict[str, Any] = {"question": "请研究 AI 行业趋势", "token_used": 50}
        result = await clarifier.run(state, deps=None)
        assert "token_used" not in result
        assert state["token_used"] == 50

    def test_missing_answers_key(self) -> None:
        assert clarifier.clarification_to_state_payload({"other": 1}) == {}

    def test_answers_not_dict(self) -> None:
        assert clarifier.clarification_to_state_payload({"answers": "x"}) == {}


# ---------------------------------------------------------------------------
# 判定口径与调用参数（2026-09-12 联调校准后的 M1 契约）
# ---------------------------------------------------------------------------


class TestClarifierPromptCalibration:
    def test_prompt_defaults_to_pass_with_hard_gate(self) -> None:
        """提示词必须保持"默认放行、仅硬歧义追问"口径，防止回退为过严判定。"""
        prompt = clarifier._SYSTEM_PROMPT_ZH
        assert "默认放行" in prompt
        assert "不得因此追问" in prompt
        # 开放式研究问法被显式列为放行示例
        assert "技术趋势" in prompt

    @pytest.mark.asyncio
    async def test_call_llm_uses_zero_temperature(self) -> None:
        """澄清判定必须以 temperature=0 调用，保证同问同判。"""
        captured: dict[str, Any] = {}

        class _CapturingClient(LLMClient):
            async def complete_structured(self, **kwargs: Any) -> Any:  # type: ignore[override]
                captured.update(kwargs)
                from app.provider.client import StructuredCompletion

                return StructuredCompletion(
                    parsed=ClarificationSchema(
                        requires_user_input=False,
                        questions=[],
                        defaults={},
                        structured_question={"goal": "g", "scope": "s"},
                    ),
                    usage={"total_tokens": 1},
                    model="fake",
                    raw="",
                )

        deps = NodeDeps(run_id="r9", team_id="t9", trace_id="tr9", llm=_CapturingClient())
        await clarifier._call_llm(deps=deps, question="任意研究问题")
        assert captured["temperature"] == clarifier._CLARIFY_TEMPERATURE == 0.0


# ---------------------------------------------------------------------------
# 协议兼容
# ---------------------------------------------------------------------------


def test_fake_provider_satisfies_protocol() -> None:
    provider = _FakeLLMProvider()
    assert isinstance(provider, LLMProvider)
