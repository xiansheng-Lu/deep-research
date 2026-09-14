"""await_human 节点单元测试（M2-2 Task 5 / M2-5 澄清回流修复）。

覆盖 critique 回流：从 human_input.answers.verdicts 解析裁决、按 conflict_id
去重、conflict.verdicts WS 帧推送；M2-5 起 clarify 分支把 answers 合并进
state.clarification 并关闭 needs_clarification，防止恢复后重复挂起。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes import await_human
from app.orchestrator.state import ResearchState, VerdictDict
from app.realtime.hub import RealtimeHub


def _state(
    *,
    reason: str = "critique",
    human_input: dict[str, Any] | None = None,
    verdicts: list[VerdictDict] | None = None,
) -> ResearchState:
    return {
        "run_id": "run_test",
        "interrupt_reason": reason,
        "human_input": human_input,
        "verdicts": verdicts or [],
    }


def _answers(verdicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"answers": {"verdicts": verdicts}}


class TestParseVerdicts:
    @pytest.mark.asyncio
    async def test_clarify_branch_without_answers_closes_gate(self) -> None:
        """空 answers（入口已 422 拦截，此处兜底）：关追问标记但不构造 clarification。"""
        patch = await await_human.run(_state(reason="clarify", human_input={"answers": {}}))
        assert patch == {"interrupt_reason": "clarify", "needs_clarification": False}

    @pytest.mark.asyncio
    async def test_clarify_branch_merges_answers_into_clarification(self) -> None:
        """M2-5 AC-5：非空 answers 合并为 clarification._human_answers，回流不再二次挂起。"""
        patch = await await_human.run(_state(reason="clarify", human_input={"answers": {"scope": "近三年"}}))
        assert patch["interrupt_reason"] == "clarify"
        assert patch["needs_clarification"] is False
        assert patch["clarification"] == {"_human_answers": {"scope": "近三年"}}

    @pytest.mark.asyncio
    async def test_no_human_input_returns_empty_verdicts(self) -> None:
        patch = await await_human.run(_state())
        assert patch == {"interrupt_reason": "critique", "verdicts": []}

    @pytest.mark.asyncio
    async def test_parses_verdicts_with_choice_reason_note(self) -> None:
        patch = await await_human.run(
            _state(
                human_input=_answers(
                    {
                        "c1": {"choice": "evidence_a", "reason": "官方来源更可信", "user_id": "u9"},
                        "c2": {"choice": "both", "reason": "口径不同", "additional_note": "已在局限说明"},
                    }
                )
            )
        )
        verdicts = patch["verdicts"]
        assert {v["conflict_id"] for v in verdicts} == {"c1", "c2"}
        v1 = next(v for v in verdicts if v["conflict_id"] == "c1")
        assert v1["choice"] == "evidence_a"
        assert v1["reason"] == "官方来源更可信"
        assert v1["user_id"] == "u9"
        v2 = next(v for v in verdicts if v["conflict_id"] == "c2")
        assert v2["additional_note"] == "已在局限说明"

    @pytest.mark.asyncio
    async def test_dedups_existing_verdicts_and_garbage(self) -> None:
        """重放安全：已在 state 的裁决不重复追加；脏条目（非 dict）跳过。"""
        existing: VerdictDict = {"conflict_id": "c1", "user_id": "u9", "choice": "reject", "reason": "旧"}
        patch = await await_human.run(
            _state(
                human_input={"answers": {"verdicts": {"c1": {"choice": "reject"}, "c2": "not-a-dict"}}},
                verdicts=[existing],
            )
        )
        assert patch["verdicts"] == [existing]

    @pytest.mark.asyncio
    async def test_works_without_deps(self) -> None:
        patch = await await_human.run(
            _state(human_input=_answers({"c1": {"choice": "evidence_a", "reason": "x"}})),
            deps=None,
        )
        assert len(patch["verdicts"]) == 1


class TestVerdictsFrame:
    @pytest.mark.asyncio
    async def test_emits_one_conflict_verdicts_frame(self) -> None:
        hub = RealtimeHub()
        frames: list[dict[str, Any]] = []

        async def _collect() -> None:
            async for event in hub.subscribe("runs:run_test"):
                frames.append(event)
                break

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)
        deps = NodeDeps(run_id="run_test", team_id="t1", trace_id="tr1", hub=hub)
        await await_human.run(
            _state(human_input=_answers({"c1": {"choice": "evidence_a", "reason": "x"}})),
            deps=deps,
        )
        await asyncio.wait_for(task, timeout=1.0)

        assert len(frames) == 1
        assert frames[0]["type"] == "conflict.verdicts"
        assert frames[0]["run_id"] == "run_test"
        assert frames[0]["payload"] == {"verdicts": [{"conflict_id": "c1", "choice": "evidence_a"}]}

    @pytest.mark.asyncio
    async def test_no_frame_without_new_verdicts(self) -> None:
        hub = RealtimeHub()

        async def _expect_empty() -> None:
            async for event in hub.subscribe("runs:run_test"):
                raise AssertionError(f"无新裁决不应推送帧，收到：{event}")

        task = asyncio.create_task(_expect_empty())
        await asyncio.sleep(0)
        deps = NodeDeps(run_id="run_test", team_id="t1", trace_id="tr1", hub=hub)
        await await_human.run(_state(), deps=deps)
        await asyncio.sleep(0.05)
        task.cancel()
