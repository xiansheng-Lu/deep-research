"""冲突审视域 Pydantic Schema（M2-2 FR-6/7/8/9）。

字段与前端 ``frontend/src/services/api/types.ts`` 的
``ConflictResponse`` / ``VerdictRequest`` / ``VerdictResponse`` 对齐；
详情响应在此之外内嵌双方证据摘要（FR-7 八项）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 冲突类型四值（与 critic system prompt / Conflict.type 枚举一致）
ConflictType = Literal["factual", "methodological", "temporal", "perspective"]
# 严重度三值
ConflictSeverity = Literal["low", "medium", "high"]
# 冲突状态机
ConflictStatus = Literal["detected", "awaiting_human", "resolved", "abandoned"]
# 裁决四值
VerdictChoice = Literal["evidence_a", "evidence_b", "both", "reject"]


class ConflictResponse(BaseModel):
    """冲突对象（列表项与 WS 帧载荷的字段超集）。"""

    id: str
    run_id: str
    claim: str
    evidence_a_id: str
    evidence_b_id: str
    type: ConflictType
    severity: ConflictSeverity
    status: ConflictStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ConflictEvidenceSummary(BaseModel):
    """冲突详情内嵌的一方证据摘要（FR-7 八项）。"""

    id: str
    title: str
    url: str
    domain: str
    snippet: str
    credibility: str
    source_type: str
    published_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ConflictDetailResponse(ConflictResponse):
    """冲突详情：冲突字段 + 双方证据摘要。"""

    evidence_a: ConflictEvidenceSummary
    evidence_b: ConflictEvidenceSummary


class VerdictRequest(BaseModel):
    """``POST /conflicts/{id}/verdict`` 请求体。"""

    choice: VerdictChoice
    reason: str = Field(min_length=1, max_length=2048)
    additional_note: str | None = Field(default=None, max_length=2048)

    @field_validator("reason")
    @classmethod
    def _reason_must_be_non_blank(cls, value: str) -> str:
        """reason 必填非空：strip 后为空串一律 422（FR-8）。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("裁决理由不能为空")
        return stripped

    @field_validator("additional_note")
    @classmethod
    def _normalize_note(cls, value: str | None) -> str | None:
        """additional_note 可空：去空白后为空串归一为 None。"""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class VerdictResponse(BaseModel):
    """裁决提交成功响应（FR-8 三字段）。"""

    conflict_id: str
    status: ConflictStatus
    verdict_id: str


__all__ = [
    "ConflictDetailResponse",
    "ConflictEvidenceSummary",
    "ConflictResponse",
    "ConflictSeverity",
    "ConflictStatus",
    "ConflictType",
    "VerdictChoice",
    "VerdictRequest",
    "VerdictResponse",
]
