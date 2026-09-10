"""研究状态 TypedDict 与阶段枚举。

研究流程包含 6 个阶段：clarify → decompose → retrieve → standardize → critique → report，
通过 LangGraph StateGraph 串联，所有节点读写 ResearchState。

字段集以《AI研究者助手-后端详细设计》§6.2 为权威基线，增删字段须同步文档。
"""

from enum import StrEnum
from typing import Literal, NotRequired, TypedDict


class ResearchStage(StrEnum):
    """研究流程阶段。"""

    CLARIFY = "clarify"
    DECOMPOSE = "decompose"
    RETRIEVE = "retrieve"
    STANDARDIZE = "standardize"
    CRITIQUE = "critique"
    REPORT = "report"


SubQuestionStatus = Literal["pending", "queued", "running", "succeeded", "failed", "evidence_short"]
StageName = Literal["clarify", "decompose", "retrieve", "standardize", "critique", "report"]


class SubQuestionDict(TypedDict):
    """子问题（§6.2）。"""

    id: str
    question: str
    depends_on: list[str]
    status: SubQuestionStatus
    evidence_ids: list[str]


class EvidenceDict(TypedDict):
    """证据条目（§6.2）。"""

    id: str
    sub_question_id: str
    url: str
    domain: str
    title: str
    snippet: str
    source_type: str
    source_level: str
    credibility: str
    fingerprint: str
    published_at: str | None
    fetched_at: str


class ConflictDict(TypedDict):
    """分歧条目（§6.2）。"""

    id: str
    claim: str
    evidence_a_id: str
    evidence_b_id: str
    type: str
    severity: str
    status: Literal["detected", "awaiting_human", "resolved", "abandoned"]


class VerdictDict(TypedDict):
    """用户裁决（§6.2）。"""

    conflict_id: str
    user_id: str
    choice: str
    reason: str | None


class ReportClaim(TypedDict):
    """报告论断（§6.2）。"""

    id: str
    text: str
    confidence: Literal["single_source", "cross_verified", "inferred"]
    citations: list[dict]


class ResearchState(TypedDict, total=False):
    """LangGraph 状态图的全局状态。

    字段命名与类型以《后端详细设计》§6.2 为权威基线，修改需同步文档。
    """

    # ===== 标识 =====
    run_id: str
    project_id: str

    # ===== 输入 =====
    question: str
    clarification: dict | None
    template_id: str
    tier: str  # quick / standard / deep / extreme

    # ===== 阶段产物 =====
    sub_questions: list[SubQuestionDict]
    evidence: list[EvidenceDict]
    standardized_evidence: list[EvidenceDict]
    conflicts: list[ConflictDict]
    verdicts: list[VerdictDict]

    report_outline: list[dict]
    report_claims: list[ReportClaim]
    report_draft: str

    # ===== 编排 =====
    current_stage: StageName
    stage_attempts: dict[str, int]
    needs_clarification: NotRequired[bool]      # 阶段1 澄清 HITL 标记（§6.3 / §6.5.2）
    interrupt_reason: NotRequired[str]          # clarify | critique —— 区分 await_human 回流路径
    interrupt_payload: NotRequired[dict]        # await_human 挂起时向用户展示的上下文
    human_input: dict | None

    # ===== 治理 =====
    token_used: int
    token_budget: int

    # ===== 时间与追踪 =====
    started_at: str
    finished_at: str | None
    trace_id: str                       # 链路追踪 ID（§12.1.2），由 API 层写入
    failure_reason: str | None          # 节点异常兜底记录（§6.5.1 / nodes._base.instrument）
    updated_at: str                     # 最近一次状态更新时间（UTC ISO 8601）
