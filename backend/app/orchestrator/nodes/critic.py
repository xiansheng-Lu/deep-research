"""审视节点（§6.5.6 critic）。

契约：
- 输入：``ResearchState`` 含 ``standardized_evidence``（WP-5.5 产出），
    可选已有 ``report_claims`` / ``conflicts`` / ``verdicts``（用于回流）。
- 输出：合并到 state 的字段包括 ``report_claims`` / ``conflicts`` /
    ``verdicts`` / ``interrupt_reason`` / ``interrupt_payload`` /
    ``current_stage`` / ``updated_at``。
- 行为：
    1. ``generate_draft_claims`` —— M1 简化版：每条 evidence 派生一条
       ``ReportClaim``（confidence=single_source + 1 条引用）。
    2. ``detect_conflicts`` —— M1 启发式：两两 evidence 对 ``title + snippet``
       做词集合 Jaccard 相似度（>= 阈值）且可信度不同 → 冲突候选。
    3. ``is_resolvable`` —— severity in {"low", "medium"} → 可自动解决；
       "high" → 需用户裁决。
    4. ``resolve_by_authority`` —— 选 credibility 高的对应 claim；
       平局时按 report_claims 顺序保留首条。
    5. 循环至多 ``MAX_CRITIC_ITERATIONS=3`` 轮；
       三类收敛信号（任一触发即停）：无新冲突 / token_used 超过
       budget×CRITIC_BUDGET_RATIO=0.30 / 迭代满。
    6. 有冲突且无裁决 → 写 ``interrupt_reason="critique"`` + ``interrupt_payload``，
       等待 ``await_human`` 经用户裁决后回流收敛。
- 显式跳过：``persist_conflict`` ORM 落库（M1 暂不实现）、LLM 调用的精确冲突检测
    （M1 用启发式；M2+ 再接入语义判定）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import (
    ConflictDict,
    EvidenceDict,
    ReportClaim,
    ResearchStage,
    ResearchState,
)

log = get_logger("orchestrator.critic")

# ---------------------------------------------------------------------------
# 常量（§6.5.6）
# ---------------------------------------------------------------------------

MAX_CRITIC_ITERATIONS: int = 3
"""审视节点最多迭代轮数。"""

CRITIC_BUDGET_RATIO: float = 0.30
"""审视阶段消耗的 budget 比例上限（占档位 token 预算 30%）。"""

CONFLICT_SIMILARITY_THRESHOLD: float = 0.4
"""两两 evidence 的 Jaccard 词集合相似度阈值；高于该值才视为同一议题。"""

_CONFLICT_TYPE_DEFAULT: str = "factual"
_CONFLICT_SEVERITY_DEFAULT: str = "medium"

_CREDIBILITY_RANK: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}

# ---------------------------------------------------------------------------
# 文本 → 词集合（M1 简化版：去标点 + 小写 + 长度≥2 的 token）
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def _tokenize(text: str | None) -> set[str]:
    """把文本切成词集合：英文 / 数字按连续段拆，CJK 按单字拆。

    全量保留（含单字），让 Jaccard 召回主要靠 CJK 单字重叠；
    噪声由相似度阈值天然过滤。
    """
    if not text:
        return set()
    tokens: set[str] = set()
    for m in _TOKEN_RE.findall(text.lower()):
        # CJK / 短串按单字拆；英文连续段也按字拆，便于 Jaccard 召回
        for ch in m:
            tokens.add(ch)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度：|A∩B| / |A∪B|；空集约定返回 0.0。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# 草稿论断（M1 简化版）
# ---------------------------------------------------------------------------


def generate_draft_claims(material: list[EvidenceDict]) -> list[ReportClaim]:
    """每条 evidence 派生一条 ReportClaim。

    M1 简化策略：
    - 1 条 evidence → 1 条 claim；
    - confidence 固定 ``single_source``（无 cross_verified 语义证据前不夸口）；
    - citations 仅挂 1 条引用（evidence_id / url / title / snippet）。
    """
    claims: list[ReportClaim] = []
    for ev in material:
        claim_text = ev.get("title") or ev.get("snippet") or ""
        citations: list[dict[str, Any]] = [
            {
                "evidence_id": ev["id"],
                "url": ev["url"],
                "title": ev["title"],
                "snippet": ev["snippet"],
            }
        ]
        claims.append(
            {
                "id": new_ulid(),
                "text": claim_text,
                "confidence": "single_source",
                "citations": citations,
            }
        )
    return claims


# ---------------------------------------------------------------------------
# 冲突检测（M1 启发式）
# ---------------------------------------------------------------------------


def detect_conflicts(
    claims: list[ReportClaim],
    material: list[EvidenceDict],
) -> list[ConflictDict]:
    """M1 冲突检测：两两 evidence 计算 Jaccard 相似度 + 可信度不同。

    冲突构造规则：
    - 相似度 >= ``CONFLICT_SIMILARITY_THRESHOLD`` 视为同一议题；
    - 两 evidence credibility 不同 → 视为潜在冲突；
    - type/severity 采用默认值（M1 暂不细分）。
    """
    if len(material) < 2:
        return []

    # 预计算每条 evidence 的词集合
    tokens_per_ev: list[set[str]] = []
    for ev in material:
        bag = _tokenize(ev.get("title")) | _tokenize(ev.get("snippet"))
        tokens_per_ev.append(bag)

    conflicts: list[ConflictDict] = []
    n = len(material)
    for i in range(n):
        for j in range(i + 1, n):
            ev_i = material[i]
            ev_j = material[j]
            sim = _jaccard(tokens_per_ev[i], tokens_per_ev[j])
            if sim < CONFLICT_SIMILARITY_THRESHOLD:
                continue
            cred_i = ev_i.get("credibility") or "D"
            cred_j = ev_j.get("credibility") or "D"
            if cred_i == cred_j:
                continue
            # 生成冲突
            claim_text = ev_i.get("title") or ev_j.get("title") or ""
            conflicts.append(
                serialize_conflict(
                    {
                        "claim": claim_text,
                        "evidence_a_id": ev_i["id"],
                        "evidence_b_id": ev_j["id"],
                        "type": _CONFLICT_TYPE_DEFAULT,
                        "severity": _CONFLICT_SEVERITY_DEFAULT,
                    }
                )
            )

    return conflicts


def serialize_conflict(payload: dict[str, Any]) -> ConflictDict:
    """把启发式产出的 raw 冲突字典规整为 ConflictDict。

    ``payload`` 期望字段：claim / evidence_a_id / evidence_b_id / type / severity。
    """
    return {
        "id": new_ulid(),
        "claim": payload.get("claim") or "",
        "evidence_a_id": payload["evidence_a_id"],
        "evidence_b_id": payload["evidence_b_id"],
        "type": payload.get("type") or _CONFLICT_TYPE_DEFAULT,
        "severity": payload.get("severity") or _CONFLICT_SEVERITY_DEFAULT,
        "status": "detected",
    }


# ---------------------------------------------------------------------------
# 裁决策略
# ---------------------------------------------------------------------------


def is_resolvable(conflict: ConflictDict) -> bool:
    """``low`` / ``medium`` 视为可自动解决；``high`` 留给用户裁决。"""
    return conflict.get("severity") in {"low", "medium"}


def resolve_by_authority(
    claims: list[ReportClaim],
    conflict: ConflictDict,
    material: Iterable[EvidenceDict],
) -> list[ReportClaim]:
    """按可信度裁决：保留 credibility 高的 evidence 对应 claim，丢弃另一条。

    规则：
    - 若 conflict 的两条 evidence credibility 不同，保留 rank 较小的对应 claim；
    - 平局时按 claim 出现顺序保留首条（M1 启发式约定）。
    - 若 evidence 不在 material 中 → 不动 claims。
    """
    ev_by_id: dict[str, EvidenceDict] = {ev["id"]: ev for ev in material}
    a = ev_by_id.get(conflict["evidence_a_id"])
    b = ev_by_id.get(conflict["evidence_b_id"])
    if a is None or b is None:
        return claims

    rank_a = _CREDIBILITY_RANK.get(a.get("credibility") or "D", 3)
    rank_b = _CREDIBILITY_RANK.get(b.get("credibility") or "D", 3)
    winner_ev_id = (
        conflict["evidence_a_id"] if rank_a <= rank_b else conflict["evidence_b_id"]
    )

    # 找到 winner_ev_id 对应的 claim（M1：claims 与 material 1:1 对齐）
    # 精确做法：每条 claim 的 citations[*].evidence_id 等于对应 evidence id。
    kept: list[ReportClaim] = []
    for c in claims:
        cite_ev_ids = {cit.get("evidence_id") for cit in (c.get("citations") or [])}
        if winner_ev_id in cite_ev_ids:
            kept.append(c)
    return kept


# ---------------------------------------------------------------------------
# 节点入口
# ---------------------------------------------------------------------------


def _budget_exhausted(state: ResearchState) -> bool:
    """检查 token_used 是否超过 budget × CRITIC_BUDGET_RATIO。"""
    used = int(state.get("token_used") or 0)
    budget = int(state.get("token_budget") or 0)
    if budget <= 0:
        return False
    return used > budget * CRITIC_BUDGET_RATIO


@instrument(ResearchStage.CRITIQUE)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """审视节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中。

    M1 简化：
    - 不调 LLM（启发式检测）；
    - 不持久化 conflict（state 内累积，下一阶段 reporter 可消费）；
    - 不发 event_bus 事件（WP-6 才接 event bus）。
    """
    # 兼容 M0 签名：未注入 deps 时仍可运行
    del deps

    material: list[EvidenceDict] = list(state.get("standardized_evidence") or [])
    claims: list[ReportClaim] = list(state.get("report_claims") or [])
    conflicts: list[ConflictDict] = list(state.get("conflicts") or [])
    verdicts: list[dict[str, Any]] = list(state.get("verdicts") or [])

    # 若 material 非空但还没派生过 claim（首次进入）→ 派生一次
    if not claims and material:
        claims = generate_draft_claims(material)

    # 跳出条件之一：material 不足两条或预算已耗尽 → 直接进入裁决/收敛出口
    if len(material) < 2 or _budget_exhausted(state):
        if conflicts and not verdicts:
            return {
                "conflicts": conflicts,
                "verdicts": verdicts,
                "report_claims": claims,
                "interrupt_reason": "critique",
                "interrupt_payload": {
                    "conflict_ids": [c["id"] for c in conflicts],
                },
            }
        return {
            "conflicts": conflicts,
            "verdicts": verdicts,
            "report_claims": claims,
        }

    # 迭代：detect → resolve（可解）/ 累积（不可解）→ 三类收敛跳出
    iteration = 0
    while iteration < MAX_CRITIC_ITERATIONS:
        iteration += 1
        new_conflicts = detect_conflicts(claims, material)
        if not new_conflicts:
            break

        for c in new_conflicts:
            if is_resolvable(c):
                claims = resolve_by_authority(claims, c, material)
            else:
                conflicts.append(c)

        if _budget_exhausted(state):
            break

    # 出口：存在冲突且尚无用户裁决 → 写 interrupt 字段
    if conflicts and not verdicts:
        return {
            "conflicts": conflicts,
            "verdicts": verdicts,
            "report_claims": claims,
            "interrupt_reason": "critique",
            "interrupt_payload": {
                "conflict_ids": [c["id"] for c in conflicts],
            },
        }
    return {
        "conflicts": conflicts,
        "verdicts": verdicts,
        "report_claims": claims,
    }


__all__ = [
    "run",
    "generate_draft_claims",
    "detect_conflicts",
    "is_resolvable",
    "resolve_by_authority",
    "serialize_conflict",
    "MAX_CRITIC_ITERATIONS",
    "CRITIC_BUDGET_RATIO",
    "CONFLICT_SIMILARITY_THRESHOLD",
]
