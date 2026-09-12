"""意图路由 Agent 单元测试（M2-1）。

覆盖：LLM 判别三分类、手动强制、保守降级（无 LLM / 异常 / 超时）、
推荐档位启发式；全部使用替身，不发真实网络请求。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.intent_router import (
    IntentDecision,
    classify_intent,
    recommend_tier,
)
from app.orchestrator.schemas import IntentClassification
from app.provider.client import LLMClient, StructuredCompletion
from app.quota.tiers import Tier, tier_budget


class _FakeStructuredLLM(LLMClient):
    """按预设结果返回结构化判别，可记录调用参数。"""

    def __init__(self, result: IntentClassification | None = None) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def complete_structured(self, **kwargs: Any) -> StructuredCompletion:  # type: ignore[override]
        self.calls.append(kwargs)
        assert self._result is not None
        return StructuredCompletion(
            parsed=self._result,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model="fake-intent",
            raw="{}",
        )


class _BoomLLM(LLMClient):
    async def complete_structured(self, **kwargs: Any) -> StructuredCompletion:  # type: ignore[override]
        raise RuntimeError("deepseek 500")


class _SlowLLM(LLMClient):
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def complete_structured(self, **kwargs: Any) -> StructuredCompletion:  # type: ignore[override]
        import asyncio

        await asyncio.sleep(self.delay)
        raise AssertionError("不应执行到返回")


def _classification(intent: str, confidence: float = 0.9, reason: str = "依据") -> IntentClassification:
    return IntentClassification(intent=intent, confidence=confidence, reason=reason)  # type: ignore[arg-type]


class TestClassifyByLLM:
    @pytest.mark.asyncio
    async def test_chat_intent_has_no_research_params(self) -> None:
        llm = _FakeStructuredLLM(_classification("chat", 0.95, "问候语"))
        decision = await classify_intent("你好呀", llm=llm)

        assert decision.intent == "chat"
        assert decision.source == "llm"
        assert decision.degraded is False
        assert decision.confidence == 0.95
        # 闲聊路径不携带任何研究推荐参数
        assert decision.recommended_template is None
        assert decision.recommended_tier is None
        assert decision.estimated_token_budget is None

    @pytest.mark.asyncio
    async def test_research_intent_carries_tier_recommendation(self) -> None:
        llm = _FakeStructuredLLM(_classification("research", 0.92, "含对比对象"))
        text = "对比 PostgreSQL 与 MongoDB 在 JSON 查询场景下的索引差异，给出选型建议"
        decision = await classify_intent(text, llm=llm)

        assert decision.intent == "research"
        assert decision.source == "llm"
        assert decision.recommended_template == "generic"
        assert decision.recommended_tier == Tier.STANDARD.value
        assert decision.estimated_token_budget == tier_budget(Tier.STANDARD)
        assert decision.estimated_cost_grade == "standard"

    @pytest.mark.asyncio
    async def test_uncertain_intent_uses_research_params_conservatively(self) -> None:
        """uncertain 保守走研究：保留 intent=uncertain，但带研究推荐参数。"""
        llm = _FakeStructuredLLM(_classification("uncertain", 0.4, "只有名词"))
        decision = await classify_intent("新能源", llm=llm)

        assert decision.intent == "uncertain"
        assert decision.recommended_template == "generic"
        assert decision.recommended_tier is not None
        assert decision.estimated_token_budget is not None
        assert decision.degraded is False

    @pytest.mark.asyncio
    async def test_called_with_zero_temperature(self) -> None:
        llm = _FakeStructuredLLM(_classification("chat"))
        await classify_intent("在吗", llm=llm)
        assert llm.calls[0]["temperature"] == 0.0


class TestForcedIntent:
    @pytest.mark.asyncio
    async def test_force_chat_bypasses_llm(self) -> None:
        llm = _FakeStructuredLLM(_classification("research"))
        decision = await classify_intent("随便研究点什么", llm=llm, force="chat")

        assert decision.intent == "chat"
        assert decision.source == "forced"
        assert decision.confidence == 1.0
        assert llm.calls == []  # 强制路径不调用模型

    @pytest.mark.asyncio
    async def test_force_research_bypasses_llm(self) -> None:
        llm = _FakeStructuredLLM(_classification("chat"))
        decision = await classify_intent("你好", llm=llm, force="research")

        assert decision.intent == "research"
        assert decision.source == "forced"
        assert decision.estimated_token_budget is not None
        assert llm.calls == []


class TestConservativeFallback:
    @pytest.mark.asyncio
    async def test_none_llm_falls_back_to_research(self) -> None:
        decision = await classify_intent("任意问题", llm=None)

        assert decision.intent == "research"
        assert decision.source == "fallback"
        assert decision.degraded is True
        assert decision.confidence == 0.0
        assert decision.estimated_token_budget is not None

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_research(self) -> None:
        decision = await classify_intent("任意问题", llm=_BoomLLM())

        assert decision.intent == "research"
        assert decision.source == "fallback"
        assert decision.degraded is True

    @pytest.mark.asyncio
    async def test_llm_timeout_falls_back_to_research(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 用极小超时 + 慢调用确定性触发超时分支
        from app.agents import intent_router

        monkeypatch.setattr(intent_router, "_CLASSIFY_TIMEOUT_S", 0.05)
        decision = await classify_intent("任意问题", llm=_SlowLLM(0.3))

        assert decision.intent == "research"
        assert decision.source == "fallback"
        assert decision.degraded is True


class TestTierRecommendation:
    def test_short_question_recommends_quick(self) -> None:
        assert recommend_tier("什么是 RAG") == Tier.QUICK

    def test_long_question_recommends_standard(self) -> None:
        long_text = "对比两类主流向量数据库在大规模检索场景下的性能、成本与运维差异"
        assert recommend_tier(long_text) == Tier.STANDARD

    @pytest.mark.asyncio
    async def test_short_research_question_gets_quick_budget(self) -> None:
        llm = _FakeStructuredLLM(_classification("research"))
        decision = await classify_intent("光伏行业趋势", llm=llm)
        assert decision.recommended_tier == Tier.QUICK.value
        assert decision.estimated_token_budget == tier_budget(Tier.QUICK)


def test_decision_is_pydantic_model() -> None:
    decision = IntentDecision(
        intent="chat",
        confidence=1.0,
        source="forced",
    )
    assert decision.intent == "chat"
