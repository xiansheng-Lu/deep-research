"""标准化节点（§6.5.5 standardizer，M2-6 增强）。

契约：
- 输入：``ResearchState`` 含 ``evidence``（retrieve 阶段产出的 ``EvidenceDict``）
  与 ``sub_questions``（收敛 ``evidence_ids`` 引用）。
- 输出：合并到 state 的字段包括 ``standardized_evidence`` / ``sub_questions`` /
    ``current_stage`` / ``updated_at``。
- 行为（M2-6）：
    1. 跨子问题按 ``fingerprint`` 去重，保留首次出现的证据。
    2. ``retrieval.source_rules.classify_source`` 按域名规则表落定
       ``source_type``（official_doc/news/community/search）与
       ``source_level``（primary/secondary/tertiary）。
    3. ``classify_credibility`` 按「权威基准 + relevance 至多降一档 +
       primary 地板 B」推导 A/B/C/D（§5.4，可信度与相关性解耦）。
    4. 写 ``metadata_`` 留痕：命中规则、缺失字段（合并 fan-out 已写的
       相关性/发布时间来源）。
    5. 排序 credibility 升序（A→D）+ 同级 relevance 降序；收敛子问题引用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from app.core.logging import get_logger
from app.db.models.evidence import Evidence
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.persistence import persist_evidence, persist_sub_questions
from app.orchestrator.state import (
    EvidenceDict,
    ResearchStage,
    ResearchState,
    SubQuestionDict,
)
from app.retrieval.source_rules import SourceClassification, classify_source

if TYPE_CHECKING:
    from app.realtime.hub import RealtimeHub

log = get_logger("orchestrator.standardizer")

SourceLevel = Literal["primary", "secondary", "tertiary"]
Credibility = Literal["A", "B", "C", "D"]

# 可信度与相关性解耦后的相关性分界（M2-6 方案 §5.4）
_RELEVANCE_LOW_THRESHOLD = 0.2

# 来源等级 → 基础可信档（高相关时的档位）
_SOURCE_LEVEL_BASE: dict[str, Credibility] = {
    "primary": "A",
    "secondary": "B",
    "tertiary": "C",
}

_CREDIBILITY_RANK: dict[Credibility, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
_RANK_TO_CREDIBILITY: tuple[Credibility, ...] = ("A", "B", "C", "D")


def classify_source_level(domain: str | None) -> SourceLevel:
    """按域名规则表判定来源等级（保留函数名以兼容既有调用方/测试）。"""
    return classify_source(domain).source_level  # type: ignore[return-value]


def classify_credibility(ev: EvidenceDict) -> Credibility:
    """按「权威基准 + relevance 微调」推导可信度 A/B/C/D（M2-6 方案 §5.4）。

    - primary：高相关（≥0.2）A；低相关降一档至 **B（地板，不再降到 C/D）**；
    - secondary：高相关 B；低相关至多降一档至 C；
    - tertiary：高相关 C；低相关 D。

    缺失发布时间不参与降级（权威政策/技术文档常无日期）；可信度只回答
    「信源本身是否权威」，弱相关应在排序中靠后而非被否定可信度。
    """
    base = _SOURCE_LEVEL_BASE.get(ev.get("source_level") or "tertiary", "C")
    score = float(ev.get("relevance_score") or 0.0)
    steps = 0 if score >= _RELEVANCE_LOW_THRESHOLD else 1
    base_idx = _CREDIBILITY_RANK[base]
    new_idx = min(len(_CREDIBILITY_RANK) - 1, base_idx + steps)
    return _RANK_TO_CREDIBILITY[new_idx]


def _missing_fields(ev: EvidenceDict) -> list[str]:
    """汇总元数据缺失字段（PRD §8「来源信息不全」的承载）。

    domain/source_type/source_level/credibility 由本节点必有，不列入；
    当前仅发布时间可能缺失。
    """
    missing: list[str] = []
    if not ev.get("published_at"):
        missing.append("published_at")
    return missing


def dedupe_by_fingerprint(raw: list[EvidenceDict]) -> list[EvidenceDict]:
    """按 ``fingerprint`` 去重；同 fingerprint 保留首次出现的证据。"""
    if not raw:
        return []
    seen: set[str] = set()
    deduped: list[EvidenceDict] = []
    for ev in raw:
        fp = ev["fingerprint"]
        if fp in seen:
            continue
        seen.add(fp)
        deduped.append(ev)
    return deduped


def _reindex_sub_questions(
    subqs: list[SubQuestionDict],
    *,
    kept_evidence_ids: set[str],
) -> list[SubQuestionDict]:
    """收敛子问题的 ``evidence_ids``：丢弃因去重而消失的证据 ID。"""
    result: list[SubQuestionDict] = []
    for s in subqs:
        new_ids = [eid for eid in (s.get("evidence_ids") or []) if eid in kept_evidence_ids]
        result.append({**s, "evidence_ids": new_ids})
    return result


async def _publish_evidence_fetched(
    hub: RealtimeHub | None,
    *,
    run_id: str,
    row: Evidence,
) -> None:
    """对本次新增证据逐条推送 evidence.fetched（载荷取分类落库后的权威字段）。

    实时帧失败不阻断研究主链路；``content`` 不在增量载荷中（体积大，
    前端按需走 ``GET /evidence/{id}`` 拉详情，M2-4 §7）。
    """
    if hub is None:
        return
    event = {
        "type": "evidence.fetched",
        "stage": ResearchStage.STANDARDIZE.value,
        "payload": {
            "id": row.id,
            "sub_question_id": row.sub_question_id,
            "url": row.url,
            "domain": row.domain,
            "title": row.title,
            "snippet": row.snippet,
            "source_type": row.source_type,
            "source_level": row.source_level,
            "credibility": row.credibility,
            "relevance_score": float(row.relevance_score),
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "excluded_by_user": bool(row.excluded_by_user),
        },
    }
    try:
        await hub.publish(f"runs:{run_id}", event)
    except Exception as exc:  # noqa: BLE001 - 实时事件失败不阻断主链路
        log.warning(
            "evidence.fetched 推送失败",
            extra={"run_id": run_id, "evidence_id": row.id, "error": repr(exc)},
        )


@instrument(ResearchStage.STANDARDIZE)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """标准化节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中。分类完成的证据在此
    统一落库（retrieve 阶段不写证据，避免固化临时分级），子问题引用收敛结果
    同步 upsert。
    """
    raw: list[EvidenceDict] = list(state.get("evidence") or [])
    subqs: list[SubQuestionDict] = list(state.get("sub_questions") or [])

    if not raw:
        if deps is not None and deps.db_session is not None and subqs:
            await persist_sub_questions(
                deps.db_session,
                run_id=str(state.get("run_id") or ""),
                items=subqs,
            )
        return {"standardized_evidence": [], "sub_questions": subqs}

    # 1) 跨子问题去重
    deduped = dedupe_by_fingerprint(raw)

    # 2) 分类：域名规则表落定 source_type/source_level + credibility 新模型
    classified: list[EvidenceDict] = []
    for ev in deduped:
        classification: SourceClassification = classify_source(ev.get("domain"))
        new_ev: EvidenceDict = {
            **ev,
            "source_type": classification.source_type,
            "source_level": classification.source_level,
        }
        new_ev["credibility"] = classify_credibility(new_ev)
        # metadata 留痕：合并 fan-out 已写的相关性/日期来源，补分类依据与缺失字段
        meta = dict(ev.get("metadata_") or {})
        meta["source_rule"] = classification.rule
        missing = _missing_fields(new_ev)
        if missing:
            meta["missing_fields"] = missing
        else:
            meta.pop("missing_fields", None)
        new_ev["metadata_"] = meta
        classified.append(new_ev)

    # 3) 排序：按 credibility 升序（A→B→C→D）+ relevance_score 降序
    # 注：§6.5.5 原文 reverse=True 与字母序语义冲突，本实现按语义 reverse=False
    classified.sort(
        key=lambda e: (e["credibility"], -float(e.get("relevance_score") or 0.0)),
    )

    # 4) 收敛子问题引用：丢弃因去重消失的证据 ID
    kept_ids = {e["id"] for e in classified}
    updated_subs = _reindex_sub_questions(subqs, kept_evidence_ids=kept_ids)

    # 5) 落库（幂等）：子问题终态引用 + 分类后证据；无 DB 会话（纯单测）时跳过
    if deps is not None and deps.db_session is not None:
        run_id = str(state.get("run_id") or "")
        await persist_sub_questions(deps.db_session, run_id=run_id, items=updated_subs)
        added_rows = await persist_evidence(deps.db_session, run_id=run_id, items=classified)
        # 仅对本次新增证据发增量帧；重放命中既有证据不重复推送
        hub = getattr(deps, "hub", None)
        for row in added_rows:
            await _publish_evidence_fetched(hub, run_id=run_id, row=row)

    return {
        "standardized_evidence": classified,
        "sub_questions": updated_subs,
    }


__all__ = [
    "run",
    "classify_source_level",
    "classify_credibility",
    "dedupe_by_fingerprint",
    "SourceLevel",
    "Credibility",
]
