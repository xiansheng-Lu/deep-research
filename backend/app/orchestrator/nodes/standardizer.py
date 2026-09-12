"""标准化节点（§6.5.5 standardizer）。

契约：
- 输入：``ResearchState`` 含 ``evidence``（WP-5.4 检索阶段产出的 ``EvidenceDict`` 列表）
  与 ``sub_questions``（用于收敛 ``evidence_ids`` 引用）。
- 输出：合并到 state 的字段包括 ``standardized_evidence`` / ``sub_questions`` /
    ``current_stage`` / ``updated_at``。
- 行为：
    1. 跨子问题按 ``fingerprint`` 去重，保留首次出现的证据。
    2. 重新判定 ``source_level``：gov/edu/学术域 → primary；已知媒体域 → secondary；
       其余 → tertiary。
    3. 重新判定 ``credibility``：结合 ``source_level`` 与 ``relevance_score`` 推导
       A/B/C/D 评级（score>=0.8 维持基础；>=0.5 降一档；<0.5 降两档；最低 D）。
    4. 排序：按 ``(credibility, relevance_score)`` ``reverse=False`` 实现 ——
       字符串 ``A<B<C<D`` 升序恰好与"可信度高者靠前"语义一致。
       （注：§6.5.5 原文 ``reverse=True`` 在字母序下与语义冲突，
       本实现以语义为准。）
    5. 收敛 ``sub_questions[*].evidence_ids``：去除因去重而消失的证据 ID。
"""

from __future__ import annotations

from typing import Any, Literal

from app.core.logging import get_logger
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.persistence import persist_evidence, persist_sub_questions
from app.orchestrator.state import (
    EvidenceDict,
    ResearchStage,
    ResearchState,
    SubQuestionDict,
)

log = get_logger("orchestrator.standardizer")

SourceLevel = Literal["primary", "secondary", "tertiary"]
Credibility = Literal["A", "B", "C", "D"]

# ---------------------------------------------------------------------------
# 来源等级 / 可信度 启发式（M1 简化版）
# ---------------------------------------------------------------------------

# 政府 / 教育 / 学术机构 → primary
_PRIMARY_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".gov.cn",
    ".gov",
    ".edu.cn",
    ".edu",
    ".ac.cn",
    ".ac",
)

# 已知新闻媒体关键字（含二级后缀即可）→ secondary
_SECONDARY_DOMAIN_KEYWORDS: tuple[str, ...] = (
    "news",
    "press",
    "times",
    "reuters",
    "xinhua",
    "bloomberg",
    "bbc",
    "cnn",
    "nytimes",
    "wsj",
    "ft.com",
    "economist",
    "guardian",
)

# 可信度等级降序映射：基础等级 + score 阶梯 → 调整步数
_SOURCE_LEVEL_BASE: dict[SourceLevel, Credibility] = {
    "primary": "A",
    "secondary": "B",
    "tertiary": "C",
}

_CREDIBILITY_RANK: dict[Credibility, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
_RANK_TO_CREDIBILITY: tuple[Credibility, ...] = ("A", "B", "C", "D")


def classify_source_level(domain: str | None) -> SourceLevel:
    """按域名启发式判定来源等级。

    规则：
    - 空域名 / ``None`` → tertiary
    - 命中 ``_PRIMARY_DOMAIN_SUFFIXES`` → primary
    - 命中 ``_SECONDARY_DOMAIN_KEYWORDS`` 关键字 → secondary
    - 其余 → tertiary
    """
    d = (domain or "").lower().strip()
    if not d:
        return "tertiary"
    for suffix in _PRIMARY_DOMAIN_SUFFIXES:
        if d.endswith(suffix):
            return "primary"
    for kw in _SECONDARY_DOMAIN_KEYWORDS:
        if kw in d:
            return "secondary"
    return "tertiary"


def classify_credibility(ev: EvidenceDict) -> Credibility:
    """根据 ``source_level`` 与 ``relevance_score`` 推导可信度 A/B/C/D。

    规则（M1 简化版）：
    - ``source_level`` 决定基础等级：primary=A / secondary=B / tertiary=C
    - ``relevance_score`` 决定调整步数：
        - score >= 0.8 → 0 步（维持基础）
        - score >= 0.5 → 1 步
        - score < 0.5  → 2 步（最低夹到 D）
    """
    base = _SOURCE_LEVEL_BASE[ev["source_level"]]
    score = float(ev.get("relevance_score") or 0.0)
    if score >= 0.8:
        steps = 0
    elif score >= 0.5:
        steps = 1
    else:
        steps = 2
    base_idx = _CREDIBILITY_RANK[base]
    new_idx = min(len(_CREDIBILITY_RANK) - 1, base_idx + steps)
    return _RANK_TO_CREDIBILITY[new_idx]


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

    # 2) 分类：source_level + credibility
    classified: list[EvidenceDict] = []
    for ev in deduped:
        new_ev: EvidenceDict = {
            **ev,
            "source_level": classify_source_level(ev.get("domain", "")),
        }
        new_ev["credibility"] = classify_credibility(new_ev)
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
        await persist_evidence(deps.db_session, run_id=run_id, items=classified)

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
