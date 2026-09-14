"""报告查询服务：归属校验、结构化超集装配、信源索引去重排序（M2-7）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.db.models.evidence import Evidence
from app.db.models.report import Report, ReportCitation
from app.db.models.run import ResearchRun
from app.schemas.reports import (
    ReportBlock,
    ReportCitationItem,
    ReportOutlineItem,
    ReportResponse,
)


async def get_report_for_run(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: str,
) -> Report:
    """按 run_id 取报告：run 不存在/非属主 404，报告未生成 422（两端点共用）。"""
    run = await session.scalar(
        select(ResearchRun.id).where(ResearchRun.id == run_id).where(ResearchRun.creator_id == user_id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")
    report = await session.scalar(select(Report).where(Report.run_id == run_id))
    if report is None:
        raise ValidationError("报告尚未生成")
    return report


def to_response(report: Report) -> ReportResponse:
    """ORM → 响应：八字段之外从 content_json 新形态带出 outline/blocks。

    旧形态 content_json（claims/conflicts/outline，draft 部分草稿）不含 blocks 键，
    outline/blocks 缺省为空数组，不破坏既有八字段消费者。
    """
    response = ReportResponse.model_validate(report)
    payload = report.content_json if isinstance(report.content_json, dict) else {}
    blocks = payload.get("blocks")
    if isinstance(blocks, list):
        response.blocks = [ReportBlock.model_validate(block) for block in blocks if isinstance(block, dict)]
    outline = payload.get("outline")
    if isinstance(outline, list) and isinstance(blocks, list):
        response.outline = [
            ReportOutlineItem.model_validate(item) for item in outline if isinstance(item, dict)
        ]
    return response


async def list_citations(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: str,
) -> list[ReportCitationItem]:
    """报告级信源索引：引文行按 position 升序，Python 侧取证据元数据并去重（§5.9）。

    查询统一走 ``scalars``（与持久化层一致）：先取报告全部引文行（block×证据），
    再按 run 取证据池建索引；报告级同一 marker 只保留首次出现的片段。
    """
    report = await get_report_for_run(session, run_id=run_id, user_id=user_id)
    citation_rows = (
        await session.scalars(
            select(ReportCitation)
            .where(ReportCitation.report_id == report.id)
            .order_by(ReportCitation.position)
        )
    ).all()
    if not citation_rows:
        # draft/无引文报告：信源索引为空列表（不报错）
        return []
    evidence_rows = (await session.scalars(select(Evidence).where(Evidence.run_id == run_id))).all()
    evidence_index = {row.id: row for row in evidence_rows}

    by_position: dict[int, ReportCitationItem] = {}
    for citation in citation_rows:
        if citation.position in by_position:
            # 同一证据跨 block 共享 marker：报告级只保留首条，片段取首次出现
            continue
        # 引文行 evidence_id 受外键约束，证据必然在同一 run 证据池中
        evidence = evidence_index[citation.evidence_id]
        by_position[citation.position] = ReportCitationItem(
            evidence_id=evidence.id,
            marker=f"[{citation.position}]",
            snippet=citation.snippet,
            url=evidence.url,
            title=evidence.title,
            domain=evidence.domain,
            source_type=evidence.source_type,
            source_level=evidence.source_level,
            credibility=evidence.credibility,
            published_at=evidence.published_at,
        )
    return [item for _position, item in sorted(by_position.items(), key=lambda kv: kv[0])]


__all__ = ["get_report_for_run", "list_citations", "to_response"]
