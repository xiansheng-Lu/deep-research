"""WP-5.3 sub_questioner 节点单元测试：覆盖档位限制、LLM 命中、降级、ID 分配。"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes import sub_questioner
from app.orchestrator.schemas import SubQuestionItem, SubQuestionListSchema
from app.provider.base import ChatResponse
from app.provider.client import LLMClient

# ---------------------------------------------------------------------------
# 档位 → 子问题上限（§6.5.3）
# ---------------------------------------------------------------------------


class TestMaxSubQuestions:
    def test_quick(self) -> None:
        assert sub_questioner.max_sub_questions_for("quick") == 3

    def test_standard(self) -> None:
        assert sub_questioner.max_sub_questions_for("standard") == 5

    def test_deep(self) -> None:
        assert sub_questioner.max_sub_questions_for("deep") == 8

    def test_extreme(self) -> None:
        assert sub_questioner.max_sub_questions_for("extreme") == 12

    def test_unknown_falls_back_to_standard(self) -> None:
        assert sub_questioner.max_sub_questions_for("unknown") == 5
        assert sub_questioner.max_sub_questions_for(None) == 5
        assert sub_questioner.max_sub_questions_for("") == 5


# ---------------------------------------------------------------------------
# LLM 命中
# ---------------------------------------------------------------------------


class _FakeStructuredLLMClient(LLMClient):
    """直接返回预设 Schema，跳过真实 chat()。"""

    def __init__(self, parsed: SubQuestionListSchema) -> None:
        self._parsed = parsed

    async def chat(self, request: Any) -> ChatResponse:  # noqa: D401
        return ChatResponse(
            content=self._parsed.model_dump_json(),
            model="fake-model",
            usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        )


class TestSubQuestionerWithLLM:
    @pytest.mark.asyncio
    async def test_llm_returns_three_sub_questions(self) -> None:
        parsed = SubQuestionListSchema(
            sub_questions=[
                SubQuestionItem(question="Q1: 概览", depends_on=[], rationale=""),
                SubQuestionItem(question="Q2: 优势", depends_on=["1"], rationale="依赖概览"),
                SubQuestionItem(question="Q3: 风险", depends_on=["1"], rationale="依赖概览"),
            ]
        )
        deps = NodeDeps(run_id="r1", team_id="t1", trace_id="tr1", llm=_FakeStructuredLLMClient(parsed))
        state: dict[str, Any] = {
            "question": "AI 研究",
            "clarification": {"goal": "梳理 AI 进展", "scope": "近一年"},
            "tier": "quick",
        }
        result = await sub_questioner.run(state, deps=deps)
        subs = result["sub_questions"]
        assert len(subs) == 3
        assert subs[0]["question"] == "Q1: 概览"
        assert subs[0]["depends_on"] == []
        # depends_on 中的序号 1 被映射为第一条的 ULID
        assert subs[1]["depends_on"] == [subs[0]["id"]]
        assert subs[2]["depends_on"] == [subs[0]["id"]]
        # 每条都有 ULID（26 字符）+ status=pending + 空 evidence_ids
        for s in subs:
            assert len(s["id"]) == 26
            assert s["status"] == "pending"
            assert s["evidence_ids"] == []

    @pytest.mark.asyncio
    async def test_llm_returns_more_than_max_is_truncated(self) -> None:
        """LLM 偶发产出 10 条，但档位 quick 上限 3：必须截断到 3 条。"""
        parsed = SubQuestionListSchema(
            sub_questions=[
                SubQuestionItem(question=f"Q{i}", depends_on=[], rationale="")
                for i in range(1, 11)
            ]
        )
        deps = NodeDeps(run_id="r2", team_id="t2", trace_id="tr2", llm=_FakeStructuredLLMClient(parsed))
        state: dict[str, Any] = {"question": "X", "tier": "quick"}
        result = await sub_questioner.run(state, deps=deps)
        assert len(result["sub_questions"]) == 3

    @pytest.mark.asyncio
    async def test_llm_extreme_tier_accepts_twelve(self) -> None:
        parsed = SubQuestionListSchema(
            sub_questions=[
                SubQuestionItem(question=f"Q{i}", depends_on=[], rationale="")
                for i in range(1, 13)
            ]
        )
        deps = NodeDeps(run_id="r3", team_id="t3", trace_id="tr3", llm=_FakeStructuredLLMClient(parsed))
        state: dict[str, Any] = {"question": "X", "tier": "extreme"}
        result = await sub_questioner.run(state, deps=deps)
        assert len(result["sub_questions"]) == 12

    @pytest.mark.asyncio
    async def test_depends_on_with_invalid_index_is_dropped(self) -> None:
        parsed = SubQuestionListSchema(
            sub_questions=[
                SubQuestionItem(question="Q1", depends_on=[], rationale=""),
                SubQuestionItem(question="Q2", depends_on=["1", "99"], rationale=""),
                SubQuestionItem(question="Q3", depends_on=["2", "1", "abc"], rationale=""),
            ]
        )
        deps = NodeDeps(run_id="r4", team_id="t4", trace_id="tr4", llm=_FakeStructuredLLMClient(parsed))
        state: dict[str, Any] = {"question": "X", "tier": "standard"}
        result = await sub_questioner.run(state, deps=deps)
        subs = result["sub_questions"]
        # Q2 depends_on: ["1"] -> [id_of_q1]
        assert subs[1]["depends_on"] == [subs[0]["id"]]
        # Q3 depends_on: ["2", "1"] -> [id_of_q2, id_of_q1]
        assert subs[2]["depends_on"] == [subs[1]["id"], subs[0]["id"]]


# ---------------------------------------------------------------------------
# 降级路径
# ---------------------------------------------------------------------------


class TestSubQuestionerFallback:
    @pytest.mark.asyncio
    async def test_no_deps_uses_clarification_goal(self) -> None:
        state: dict[str, Any] = {
            "question": "原始问题",
            "clarification": {"goal": "澄清目标", "scope": "近一年"},
            "tier": "deep",
        }
        result = await sub_questioner.run(state, deps=None)
        subs = result["sub_questions"]
        assert len(subs) == 1
        assert subs[0]["question"] == "澄清目标"
        assert subs[0]["depends_on"] == []

    @pytest.mark.asyncio
    async def test_no_deps_no_clarification_uses_original_question(self) -> None:
        state: dict[str, Any] = {"question": "原始问题", "tier": "standard"}
        result = await sub_questioner.run(state, deps=None)
        subs = result["sub_questions"]
        assert len(subs) == 1
        assert subs[0]["question"] == "原始问题"

    @pytest.mark.asyncio
    async def test_llm_call_failure_falls_back(self) -> None:
        class _Boom(LLMClient):
            async def chat(self, request: Any) -> ChatResponse:  # noqa: D401
                raise RuntimeError("openai 502")

        deps = NodeDeps(run_id="r5", team_id="t5", trace_id="tr5", llm=_Boom())
        state: dict[str, Any] = {
            "question": "X",
            "clarification": {"goal": "G"},
            "tier": "standard",
        }
        result = await sub_questioner.run(state, deps=deps)
        assert len(result["sub_questions"]) == 1
        assert result["sub_questions"][0]["question"] == "G"

    @pytest.mark.asyncio
    async def test_llm_empty_sub_questions_falls_back(self) -> None:
        parsed = SubQuestionListSchema(sub_questions=[])
        deps = NodeDeps(run_id="r6", team_id="t6", trace_id="tr6", llm=_FakeStructuredLLMClient(parsed))
        state: dict[str, Any] = {"question": "Q", "clarification": {"goal": "G"}, "tier": "standard"}
        result = await sub_questioner.run(state, deps=deps)
        assert len(result["sub_questions"]) == 1
        assert result["sub_questions"][0]["question"] == "G"


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


class TestSubQuestionerIdempotent:
    @pytest.mark.asyncio
    async def test_existing_sub_questions_are_preserved(self) -> None:
        existing = [
            {
                "id": "01HZZZZZZZZZZZZZZZZZZZZZZZ",
                "question": "Q1",
                "depends_on": [],
                "status": "pending",
                "evidence_ids": [],
            }
        ]
        state: dict[str, Any] = {"question": "X", "tier": "standard", "sub_questions": existing}
        result = await sub_questioner.run(state, deps=None)
        assert result["sub_questions"] == existing


# ---------------------------------------------------------------------------
# 状态字段约定（§6.2 / §6.3）
# ---------------------------------------------------------------------------


class TestSubQuestionerStateContract:
    @pytest.mark.asyncio
    async def test_sub_question_fields_match_schema(self) -> None:
        state: dict[str, Any] = {"question": "X", "tier": "standard"}
        result = await sub_questioner.run(state, deps=None)
        sub = result["sub_questions"][0]
        assert set(sub.keys()) == {"id", "question", "depends_on", "status", "evidence_ids"}
        assert sub["status"] in ("pending", "queued", "running", "succeeded", "failed", "evidence_short")
        assert isinstance(sub["depends_on"], list)
        assert isinstance(sub["evidence_ids"], list)
