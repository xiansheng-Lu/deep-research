"""报告域 Pydantic Schema。

对齐 LLD §6.2 / §8.2 与前端 ``frontend/src/services/api/types.ts``：
- ``ReportResponse`` 支撑 ``GET /runs/{id}/report`` 与 ``GET /reports/{run_id}``；
  M2-7 起在原八字段之上增 ``outline`` / ``blocks`` 结构化超集（draft 旧报告为空数组）。
- ``ReportCitation`` 为 block 内联三字段引用；``ReportCitationItem`` 为
  ``GET /reports/{run_id}/citations`` 信源索引元素（关联键 + 九展示字段）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ReportStatus = Literal["draft", "final", "superseded"]
# 结构化区块四值（前端 ReportBlockType）
ReportBlockType = Literal["conclusion", "evidence", "dispute", "limitation"]
# 论断置信度三值
ClaimConfidence = Literal["single_source", "cross_verified", "inferred"]


class ReportCitation(BaseModel):
    """区块内联引用（数据点级溯源依据：角标 + 证据 + 原文片段）。"""

    evidence_id: str
    marker: str
    snippet: str


class ReportOutlineItem(BaseModel):
    """报告目录项（M2-7 语义 id：sec-overview/sec-findings/sec-disputes/sec-limitations）。"""

    id: str
    title: str
    type: str


class ReportBlock(BaseModel):
    """结构化区块（终稿渲染的唯一允许形态，对齐前端 ReportBlock）。"""

    id: str
    type: ReportBlockType
    text: str
    # conclusion/dispute 块带论断标识；evidence/limitation 块不带
    claim_id: str | None = None
    # evidence/limitation 块无置信度（与 fixture 形态一致）
    confidence: ClaimConfidence | None = None
    citations: list[ReportCitation] = []
    # 仅 dispute 块携带，关联冲突实体
    conflict_id: str | None = None


class ReportCitationItem(ReportCitation):
    """信源索引元素：内联三字段 + 回溯所需展示字段。

    线上共 10 字段：关联键 ``evidence_id`` 加九展示字段（marker/snippet/url/
    title/domain/source_type/source_level/credibility/published_at）；
    除 published_at 可空外，M2-6 后其余字段实际总有值，可选声明以前端冻结形态为准。
    """

    url: str
    title: str
    domain: str | None = None
    source_type: str | None = None
    source_level: str | None = None
    credibility: str | None = None
    published_at: datetime | None = None


class ReportResponse(BaseModel):
    """报告视图：Markdown 正文 + M2-7 结构化终稿超集。"""

    id: str
    run_id: str
    template_id: str
    status: ReportStatus = "draft"
    content_md: str
    token_used: int = 0
    created_at: datetime
    updated_at: datetime
    # M2-7：final 终稿从 content_json 带出；draft/历史旧报告为空数组
    outline: list[ReportOutlineItem] = []
    blocks: list[ReportBlock] = []

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "ClaimConfidence",
    "ReportBlock",
    "ReportBlockType",
    "ReportCitation",
    "ReportCitationItem",
    "ReportOutlineItem",
    "ReportResponse",
    "ReportStatus",
]
