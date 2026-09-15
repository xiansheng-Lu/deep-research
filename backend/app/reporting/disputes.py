"""分歧选择与双方口径的确定性原语（M2-7）。

Markdown 报告的冲突段渲染（``orchestrator.nodes.reporter``）与结构化 blocks
绑定引擎（``reporting.blocks``）共用本模块，保证两条渲染链对「哪些冲突要呈现、
双方口径文本从哪来」的判定完全一致，禁止两处复制。
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.state import ConflictDict, EvidenceDict, ReportClaim, VerdictDict

# 冲突类型枚举 -> 中文标签（与 critic system prompt §6.5.6 用词一致）
CONFLICT_TYPE_LABELS: dict[str, str] = {
    "factual": "事实性冲突",
    "methodological": "口径/方法冲突",
    "temporal": "时间错配",
    "perspective": "观点分歧",
}

# 冲突严重度枚举 -> 中文标签
SEVERITY_LABELS: dict[str, str] = {"low": "低", "medium": "中", "high": "高"}

# 裁决四值 -> 中文裁决意见
CHOICE_LABELS: dict[str, str] = {
    "evidence_a": "采纳口径 A，舍弃口径 B",
    "evidence_b": "采纳口径 B，舍弃口径 A",
    "both": "双方观点并存",
    "reject": "双方口径均不采纳",
}

# choice 取一边即视为已收敛；以下两值属于「裁决后仍保留的未消解分歧」
DIVERGENCE_CHOICES: frozenset[str] = frozenset({"both", "reject"})

# 无 verdict 且处于以下状态的冲突为「待人工裁决」
PENDING_STATUSES: frozenset[str] = frozenset({"detected", "awaiting_human"})


def build_evidence_index(
    standardized: list[EvidenceDict],
    fallback: list[EvidenceDict] | None = None,
) -> dict[str, EvidenceDict]:
    """汇总标准化证据（含未分类兜底）为 id -> evidence 索引。"""
    index: dict[str, EvidenceDict] = {}
    for ev in standardized:
        index[ev["id"]] = ev
    for ev in fallback or []:
        index.setdefault(ev["id"], ev)
    return index


def claim_for_evidence(
    claims: list[ReportClaim], evidence_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """找到引用指定证据的首条 claim，返回 (claim 文本, 命中的 citation)。"""
    for claim in claims:
        for cit in claim.get("citations") or []:
            if cit.get("evidence_id") == evidence_id:
                return (claim.get("text") or "").strip() or None, cit
    return None, None


def evidence_side(
    claims: list[ReportClaim],
    evidence_index: dict[str, EvidenceDict],
    evidence_id: str,
) -> tuple[str, str]:
    """渲染冲突一方的「口径文本 + 来源标题」（纯文本，不带 Markdown 链接）。

    口径文本优先取引用该证据的 report_claim 文本（reject 场景 claim 已被
    critic 移除），回退到证据 snippet/title；证据行缺失时保留 ID 线索。
    """
    claim_text, _citation = claim_for_evidence(claims, evidence_id)
    if claim_text is not None:
        evidence = evidence_index.get(evidence_id)
        title = ((evidence.get("title") if evidence else "") or "").strip() or "未命名来源"
        return claim_text, title
    evidence = evidence_index.get(evidence_id)
    if evidence is not None:
        text = (evidence.get("snippet") or evidence.get("title") or "").strip()
        return text or "（该侧证据无文本摘要）", (evidence.get("title") or "").strip() or "未命名来源"
    return f"（缺失证据 {evidence_id} 的文本）", f"证据 {evidence_id}"


def conflict_meta_labels(conflict: ConflictDict) -> str:
    """冲突条目后的类型/严重度中文括注。"""
    type_label = CONFLICT_TYPE_LABELS.get(conflict.get("type") or "", "冲突")
    severity_label = SEVERITY_LABELS.get(conflict.get("severity") or "", conflict.get("severity") or "")
    return f"{type_label}，严重度：{severity_label}"


def select_disputes(
    conflicts: list[ConflictDict],
    verdicts: list[VerdictDict],
) -> list[tuple[ConflictDict, VerdictDict | None]]:
    """选出需要在报告中显式呈现的冲突：待裁决 + both/reject 保留分歧。

    已自动收敛（resolved 无 verdict）与取一边的人工裁决不选；顺序与入参一致。
    """
    verdict_by_id = {v["conflict_id"]: v for v in verdicts}
    selected: list[tuple[ConflictDict, VerdictDict | None]] = []
    for conflict in conflicts:
        verdict = verdict_by_id.get(conflict["id"])
        if verdict is not None:
            if verdict.get("choice") in DIVERGENCE_CHOICES:
                selected.append((conflict, verdict))
        elif conflict.get("status") in PENDING_STATUSES:
            selected.append((conflict, None))
    return selected


def compose_dispute_text(
    conflict: ConflictDict,
    verdict: VerdictDict | None,
    *,
    claims: list[ReportClaim],
    evidence_index: dict[str, EvidenceDict],
    survivor_ids: set[str],
) -> str:
    """把单条待呈现冲突压成一段纯文本（dispute 块文本，不含 Markdown）。

    ``survivor_ids`` 为该冲突双方证据中实际进入报告材料池（未被用户剔除）的
    证据 id 集合；被剔除一侧以文字说明、不引用。
    """
    lines: list[str] = [f"议题：{conflict.get('claim') or ''}（{conflict_meta_labels(conflict)}）"]

    for side_label, evidence_id in (("A", conflict["evidence_a_id"]), ("B", conflict["evidence_b_id"])):
        if evidence_id in survivor_ids:
            text, title = evidence_side(claims, evidence_index, evidence_id)
            lines.append(f"口径 {side_label}：{text}（来源：{title}）")
        else:
            lines.append(f"口径 {side_label} 的证据已由用户剔除，不作为本报告引用来源。")

    if verdict is not None:
        choice_label = CHOICE_LABELS.get(verdict.get("choice") or "", verdict.get("choice") or "")
        lines.append(f"裁决意见：{choice_label}")
        reason = (verdict.get("reason") or "").strip()
        if reason:
            lines.append(f"裁决理由：{reason}")
        note = (verdict.get("additional_note") or "").strip() if verdict.get("additional_note") else ""
        if note:
            lines.append(f"局限备注：{note}")
    else:
        lines.append("该冲突尚待人工裁决，双方口径暂并存呈现，不应视为单一事实定论。")
    return " ".join(lines)


__all__ = [
    "CHOICE_LABELS",
    "CONFLICT_TYPE_LABELS",
    "DIVERGENCE_CHOICES",
    "PENDING_STATUSES",
    "SEVERITY_LABELS",
    "build_evidence_index",
    "claim_for_evidence",
    "compose_dispute_text",
    "conflict_meta_labels",
    "evidence_side",
    "select_disputes",
]
