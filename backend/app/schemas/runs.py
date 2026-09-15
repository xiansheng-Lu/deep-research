"""研究执行域 Pydantic Schema。

对齐 LLD §6.2 / §8.2；M1 阶段支撑 ``POST /runs`` / ``GET /runs/{id}`` /
``GET /runs/{id}/report`` 三个端点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RunTier = Literal["quick", "standard", "deep", "extreme"]
RunStatus = Literal["pending", "running", "paused", "succeeded", "failed", "cancelled"]


class CreateRunRequest(BaseModel):
    """``POST /runs`` 请求体。"""

    project_id: str = Field(min_length=1, max_length=26)
    question: str = Field(min_length=5, max_length=4096)
    tier: RunTier = Field(default="standard")
    template_id: str | None = Field(default=None, max_length=64)


class RunResponse(BaseModel):
    """研究运行视图。"""

    id: str
    project_id: str
    creator_id: str
    template_id: str
    tier: RunTier
    question: str
    status: RunStatus = "pending"
    current_stage: str | None = None
    token_used: int = 0
    token_budget: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    stream_url: str | None = None
    # M2-5：paused@clarify 时携带澄清挂起上下文，供刷新页面后恢复澄清卡
    # （不做服务端帧回放时的 REST 补齐通道）；其他状态恒为 null
    interrupt: InterruptInfo | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterruptInfo(BaseModel):
    """澄清挂起上下文（RunResponse.interrupt，对齐 interrupt.requested 帧载荷）。"""

    reason: Literal["clarify"]
    questions: list[dict[str, Any]]
    defaults: dict[str, Any] = Field(default_factory=dict)
    expires_in_seconds: int


class PauseRunRequest(BaseModel):
    """``POST /runs/{id}/pause`` 请求体（契约草案 §6.1）。"""

    reason: str | None = Field(default=None, max_length=256)


class CancelRunRequest(BaseModel):
    """``POST /runs/{id}/cancel`` 请求体（契约草案 §6.1）。"""

    reason: str | None = Field(default=None, max_length=256)
    keep_partial: bool = True


class HumanInput(BaseModel):
    """``POST /runs/{id}/resume`` 的统一人类输入模型（契约草案 §6.2）。

    ``answers`` / ``action`` / ``kind`` 三选一互斥：
    - answers：澄清/裁决挂起答案（键为问题 key，特殊键 verdicts 为裁决集合）；
    - action：主动介入（与 POST /intervene 等价，T3 开放，T2 先行 409）；
    - kind=proceed：软暂停/成本挂起后的纯恢复继续。
    """

    answers: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    kind: Literal["proceed"] | None = None

    @model_validator(mode="after")
    def _exactly_one_branch(self) -> HumanInput:
        given = [v for v in (self.answers, self.action, self.kind) if v is not None]
        if len(given) != 1:
            raise ValueError("human_input 的 answers/action/kind 必须三选一")
        return self


class ResumeRunRequest(BaseModel):
    """``POST /runs/{id}/resume`` 请求体；空 body 等价纯继续（kind=proceed）。"""

    human_input: HumanInput | None = None


InterventionActionType = Literal["ask_followup", "exclude_evidence"]


class InterventionAction(BaseModel):
    """``POST /runs/{id}/intervene`` 请求体（契约草案 §6.2）。

    type 仅开放 M2 两个动作（revert_stage/mark_doubt 为 M4）；payload 按 type
    的必填字段由 service 层校验：
    - ask_followup: {sub_question_id, question, reason?}
    - exclude_evidence: {evidence_id, reason?}
    """

    type: InterventionActionType
    payload: dict[str, Any] = Field(default_factory=dict)


class RunControlResponse(BaseModel):
    """pause/resume/cancel/intervene 控制类操作的统一响应。"""

    run_id: str
    status: RunStatus
    # cancel 且保留报告草稿时带回草稿报告 id（多数取消点无草稿，可缺省）
    partial_report_id: str | None = None


__all__ = [
    "CreateRunRequest",
    "RunResponse",
    "RunTier",
    "RunStatus",
    "InterruptInfo",
    "PauseRunRequest",
    "CancelRunRequest",
    "HumanInput",
    "ResumeRunRequest",
    "InterventionAction",
    "InterventionActionType",
    "RunControlResponse",
]
