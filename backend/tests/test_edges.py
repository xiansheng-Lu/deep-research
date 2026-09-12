"""条件边单元测试（M2-2 Task 5 重点覆盖 decide_after_critique 门控语义）。"""

from __future__ import annotations

from app.orchestrator.edges import (
    decide_after_await_human,
    decide_after_clarify,
    decide_after_cost_checkpoint,
    decide_after_critique,
)
from app.orchestrator.state import ConflictDict, ResearchState, VerdictDict


def _conflict(cid: str, *, status: str = "awaiting_human") -> ConflictDict:
    return {
        "id": cid,
        "claim": "争议议题",
        "evidence_a_id": f"{cid}-a",
        "evidence_b_id": f"{cid}-b",
        "type": "factual",
        "severity": "high",
        "status": status,  # type: ignore[typeddict-item]
    }


def _verdict(cid: str, choice: str = "evidence_a") -> VerdictDict:
    return {"conflict_id": cid, "user_id": "u1", "choice": choice, "reason": "理由"}


class TestDecideAfterCritique:
    def test_no_conflicts_goes_to_cost_checkpoint(self) -> None:
        state: ResearchState = {"conflicts": [], "verdicts": []}
        assert decide_after_critique(state) == "cost_checkpoint"

    def test_auto_resolved_conflict_does_not_suspend(self) -> None:
        """TR-5.1：low/medium 自动收敛（resolved）不挂起。"""
        state: ResearchState = {
            "conflicts": [_conflict("c1", status="resolved")],
            "verdicts": [],
        }
        assert decide_after_critique(state) == "cost_checkpoint"

    def test_pending_high_conflict_suspends(self) -> None:
        state: ResearchState = {
            "conflicts": [_conflict("c1")],
            "verdicts": [],
        }
        assert decide_after_critique(state) == "await_human"

    def test_two_high_one_verdict_still_suspends(self) -> None:
        """TR-5.4：2 条 high 仅裁决 1 条时仍判挂起。"""
        state: ResearchState = {
            "conflicts": [_conflict("c1"), _conflict("c2")],
            "verdicts": [_verdict("c1")],
        }
        assert decide_after_critique(state) == "await_human"

    def test_two_high_both_verdict_passes(self) -> None:
        """TR-5.4：2 条全部裁决后放行至成本闸门。"""
        state: ResearchState = {
            "conflicts": [_conflict("c1"), _conflict("c2")],
            "verdicts": [_verdict("c1"), _verdict("c2", choice="both")],
        }
        assert decide_after_critique(state) == "cost_checkpoint"

    def test_verdict_for_unknown_conflict_keeps_suspend(self) -> None:
        state: ResearchState = {
            "conflicts": [_conflict("c1")],
            "verdicts": [_verdict("other")],
        }
        assert decide_after_critique(state) == "await_human"


class TestOtherEdges:
    def test_decide_after_clarify(self) -> None:
        assert decide_after_clarify({"needs_clarification": True}) == "await_human"
        assert decide_after_clarify({"needs_clarification": False}) == "decompose"

    def test_decide_after_await_human_routes_by_reason(self) -> None:
        assert decide_after_await_human({"interrupt_reason": "clarify"}) == "clarify"
        assert decide_after_await_human({"interrupt_reason": "critique"}) == "critique"
        assert decide_after_await_human({}) == "critique"

    def test_decide_after_cost_checkpoint(self) -> None:
        assert decide_after_cost_checkpoint({"token_used": 95, "token_budget": 100}) == "user_intervention"
        assert decide_after_cost_checkpoint({"token_used": 10, "token_budget": 100}) == "report"
