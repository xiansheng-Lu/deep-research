"""研究状态 TypedDict 与阶段枚举。

研究流程包含 6 个阶段：clarify → decompose → retrieve → standardize → critique → report，
通过 LangGraph StateGraph 串联，所有节点读写 ResearchState。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, TypedDict
from uuid import UUID


class ResearchStage(StrEnum):
    """研究流程阶段。"""

    CLARIFY = "clarify"
    DECOMPOSE = "decompose"
    RETRIEVE = "retrieve"
    STANDARDIZE = "standardize"
    CRITIQUE = "critique"
    REPORT = "report"


class ResearchState(TypedDict, total=False):
    """LangGraph 状态图的全局状态。

    字段命名采用 snake_case，便于 LangGraph 序列化与回放。
    """

    # ===== 标识 =====
    run_id: UUID
    project_id: UUID
    user_id: UUID
    trace_id: str

    # ===== 输入 =====
    raw_query: str
    tier: str  # quick / standard / deep / extreme

    # ===== 阶段产物 =====
    clarified_query: str
    clarification_questions: list[dict[str, Any]]
    sub_questions: list[dict[str, Any]]
    evidence_items: list[dict[str, Any]]
    standardized_claims: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    verdicts: list[dict[str, Any]]
    report_outline: dict[str, Any]
    report_markdown: str

    # ===== 治理 =====
    cost_used_tokens: int
    cost_budget_tokens: int
    interrupt_requested: bool
    interrupt_reason: str | None
    failure_reason: str | None

    # ===== 时间 =====
    started_at: datetime
    updated_at: datetime
    current_stage: ResearchStage
    stage_history: list[dict[str, Any]]