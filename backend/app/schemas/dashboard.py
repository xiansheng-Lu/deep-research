"""看板域 Pydantic Schema（M2-4）。

对齐《M2-3 实时成本与 M2-4 看板接口技术方案》§6：阶段时间线、子问题、
证据池/详情、成本快照。字段与前端 ``frontend/src/services/api/types.ts``
逐字段一致；Stage 不输出 token_used/error_code/output（成本与失败原因
各有专属通道），Evidence 不输出 fingerprint/raw_storage_key/metadata。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StageName = Literal["clarify", "decompose", "retrieve", "standardize", "critique", "report"]
StageStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
SubQuestionStatus = Literal["pending", "queued", "running", "succeeded", "failed", "evidence_short"]
EvidenceSourceType = Literal["official_doc", "news", "community", "search", "internal"]
EvidenceSourceLevel = Literal["primary", "secondary", "tertiary"]
EvidenceCredibility = Literal["A", "B", "C", "D"]
CostLevel = Literal["warning", "danger"]


class StageResponse(BaseModel):
    """阶段时间线单行（固定六阶段，按 STAGE_ORDER 返回）。"""

    id: str
    run_id: str
    name: StageName
    status: StageStatus = "pending"
    attempt: int = 1
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SubQuestionResponse(BaseModel):
    """子问题看板行。"""

    id: str
    run_id: str
    question: str
    depends_on: list[str] = Field(default_factory=list)
    status: SubQuestionStatus = "pending"
    evidence_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceResponse(BaseModel):
    """证据池列表行与单条详情共用视图（``content`` 懒加载，本包通常为 null）。"""

    id: str
    run_id: str
    sub_question_id: str
    url: str
    domain: str
    title: str
    snippet: str
    content: str | None = None
    source_type: EvidenceSourceType
    source_level: EvidenceSourceLevel
    credibility: EvidenceCredibility
    relevance_score: float = 0.0
    published_at: datetime | None = None
    fetched_at: datetime
    excluded_by_user: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CostSnapshotResponse(BaseModel):
    """成本快照：直接读 run 行；``level`` 按统一阈值派生（可空）。"""

    used: int
    budget: int
    ratio: float
    level: CostLevel | None = None


__all__ = [
    "StageName",
    "StageStatus",
    "SubQuestionStatus",
    "EvidenceSourceType",
    "EvidenceSourceLevel",
    "EvidenceCredibility",
    "CostLevel",
    "StageResponse",
    "SubQuestionResponse",
    "EvidenceResponse",
    "CostSnapshotResponse",
]
