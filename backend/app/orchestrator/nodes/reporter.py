"""报告生成节点（§6.5.7 reporter）。

契约：
- 输入：``ResearchState`` 含 ``report_claims``（WP-5.6 产出）/
    ``conflicts`` / ``verdicts`` / ``question``；可选已有 ``report_outline``。
- 输出：合并到 state 的字段包括 ``report_outline`` / ``report_draft`` /
    ``current_stage`` / ``updated_at``。
- 行为：
    1. ``default_outline(template_id)`` —— 按模板 ID 返回分段大纲；
       M1 默认通用模板（4 段：背景 / 核心发现 / 冲突与不确定性 / 结论）。
    2. ``render_section`` —— M2-2 纯模板渲染，不调 LLM；
       按 section 类型拼接标题 + 关联 claim 列表 + 引用链接；
       冲突段区分「待人工裁决」与「分歧与局限」（both/reject 经裁决仍保留的
       未消解分歧），逐条给出议题、双方口径与来源、裁决理由与局限备注。
    3. ``run`` —— 遍历 outline 各分段渲染，累积为 Markdown 报告。
- 显式跳过：``stream_section`` LLM 调用（M2+ 接入）；
    ``persist_report`` ORM 落库（M1 暂不实现）；
    ``event_bus`` SSE 推送（WP-6 才接）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.logging import get_logger
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.state import (
    ConflictDict,
    EvidenceDict,
    ReportClaim,
    ResearchStage,
    ResearchState,
    VerdictDict,
)

log = get_logger("orchestrator.reporter")

# ---------------------------------------------------------------------------
# 常量：默认 outline 模板
# ---------------------------------------------------------------------------

# 通用模板：4 段。M1 阶段所有模板均沿用此骨架；M2+ 再按 template_id 分化。
DEFAULT_TEMPLATE_ID: str = "generic"

OUTLINE_TYPE_BACKGROUND: str = "background"
OUTLINE_TYPE_FINDINGS: str = "findings"
OUTLINE_TYPE_CONFLICTS: str = "conflicts"
OUTLINE_TYPE_CONCLUSION: str = "conclusion"

_DEFAULT_OUTLINE: list[dict[str, str]] = [
    {"id": "background", "title": "研究背景", "type": OUTLINE_TYPE_BACKGROUND},
    {"id": "findings", "title": "核心发现", "type": OUTLINE_TYPE_FINDINGS},
    {"id": "conflicts", "title": "冲突与不确定性", "type": OUTLINE_TYPE_CONFLICTS},
    {"id": "conclusion", "title": "结论", "type": OUTLINE_TYPE_CONCLUSION},
]

# 内部置信度枚举 -> 报告面向用户的中文标签（ReportClaim.confidence 三值）
_CONFIDENCE_LABELS: dict[str, str] = {
    "single_source": "单一来源",
    "cross_verified": "多源印证",
    "inferred": "推断",
}

# 冲突类型枚举 -> 中文标签（与 critic system prompt §6.5.6 用词一致）
_CONFLICT_TYPE_LABELS: dict[str, str] = {
    "factual": "事实性冲突",
    "methodological": "口径/方法冲突",
    "temporal": "时间错配",
    "perspective": "观点分歧",
}

# 冲突严重度枚举 -> 中文标签
_SEVERITY_LABELS: dict[str, str] = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

# 裁决四值 -> 中文裁决意见（VerdictDict.choice）
_CHOICE_LABELS: dict[str, str] = {
    "evidence_a": "采纳口径 A，舍弃口径 B",
    "evidence_b": "采纳口径 B，舍弃口径 A",
    "both": "双方观点并存",
    "reject": "双方口径均不采纳",
}

# choice 取一边即视为已收敛（冲突段不逐条展开）；以下两值属于「裁决后仍保留的
# 未消解分歧」，必须进入「分歧与局限」区块显式呈现（AC-7/AC-16）
_DIVERGENCE_CHOICES: frozenset[str] = frozenset({"both", "reject"})

# 未挂起（无 verdict）即视为待人工裁决的冲突状态
_PENDING_STATUSES: frozenset[str] = frozenset({"detected", "awaiting_human"})


def default_outline(template_id: str | None) -> list[dict[str, str]]:
    """按模板 ID 返回分段大纲。M1 阶段所有模板共用通用骨架。"""
    # M1 不分化模板；M2+ 可在此按 template_id 路由
    return [dict(s) for s in _DEFAULT_OUTLINE]


# ---------------------------------------------------------------------------
# 分段渲染（M1 简化版：纯模板拼接，不调 LLM）
# ---------------------------------------------------------------------------


def _format_citation(idx: int, citation: dict[str, Any]) -> str:
    """渲染一条引用：``[N] [标题](url)``。"""
    title = citation.get("title") or "链接"
    url = citation.get("url") or ""
    return f"[{idx}] [{title}]({url})"


def _as_text(value: Any) -> str:
    """把 LLM 结构化输出中的自由形态值归一为人类可读文本。

    DeepSeek 等模型可能把 scope/goal 返回成 dict 或 list（如
    ``{"include": [...], "exclude": [...]}``），直接插值会把 Python
    字典原文写进报告；此处统一转成中文短句。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return "、".join(_as_text(item) for item in value if _as_text(item))
    if isinstance(value, dict):
        parts: list[str] = []
        include = value.get("include") or value.get("包含") or value.get("in_scope")
        exclude = value.get("exclude") or value.get("不包含") or value.get("out_of_scope")
        if include:
            parts.append(f"包含{_as_text(include)}")
        if exclude:
            parts.append(f"不包含{_as_text(exclude)}")
        if not parts:
            # 其它键形态：键值对中文拼接
            parts = [f"{_as_text(k)}：{_as_text(v)}" for k, v in value.items() if _as_text(v)]
        return "；".join(parts)
    return str(value)


def _render_background(section: dict[str, str], state: ResearchState) -> str:
    """背景段：仅以研究问题 + 澄清目标开篇。"""
    # 用户问题常自带句末标点：已以句读符号结尾则不补句号，避免出现“。。”
    question = (state.get("question") or "").strip()
    question_ending = "" if question.endswith(("。", "！", "？", ".", "!", "?")) else "。"
    clarification = state.get("clarification") or {}
    goal = _as_text(clarification.get("goal")) if isinstance(clarification, dict) else ""
    lines = [
        f"## {section['title']}",
        "",
        f"本研究围绕以下问题展开：**{question}**{question_ending}",
    ]
    if goal:
        lines.append("")
        lines.append(f"- 研究目标：{goal}")
    scope = _as_text(clarification.get("scope")) if isinstance(clarification, dict) else ""
    if scope:
        lines.append(f"- 研究范围：{scope}")
    return "\n".join(lines) + "\n"


def _render_findings(section: dict[str, str], state: ResearchState) -> str:
    """发现段：claim 列表 + 引用编号。"""
    claims: list[ReportClaim] = list(state.get("report_claims") or [])
    lines = [f"## {section['title']}", ""]
    if not claims:
        lines.append("（暂无发现）")
        return "\n".join(lines) + "\n"
    for i, c in enumerate(claims, start=1):
        text = (c.get("text") or "").strip()
        conf_raw = c.get("confidence") or "single_source"
        conf_label = _CONFIDENCE_LABELS.get(conf_raw, conf_raw)
        # choice=both 裁决保留的 claim 带 divergence_flags：发现段显式提示读者
        # 该论断存在观点并存，双方口径见「冲突与不确定性」段（AC-16）
        divergence_hint = "（存在观点分歧，详见「冲突与不确定性」段）" if c.get("divergence_flags") else ""
        lines.append(f"{i}. {text}{divergence_hint}（置信度：{conf_label}）")
    # 引用汇总
    lines.append("")
    lines.append("**引用：**")
    for i, c in enumerate(claims, start=1):
        for cit in c.get("citations") or []:
            lines.append(_format_citation(i, cit))
    return "\n".join(lines) + "\n"


def _source_link(title: str | None, url: str | None) -> str:
    """渲染单条来源：有 url 输出 Markdown 链接，否则仅标题/域名占位。"""
    label = (title or "").strip() or "未命名来源"
    if url:
        return f"[{label}]({url})"
    return label


def _build_evidence_index(state: ResearchState) -> dict[str, EvidenceDict]:
    """汇总标准化证据（含未分类兜底）为 id -> evidence 索引。"""
    index: dict[str, EvidenceDict] = {}
    for ev in state.get("standardized_evidence") or []:
        index[ev["id"]] = ev
    for ev in state.get("evidence") or []:
        index.setdefault(ev["id"], ev)
    return index


def _claim_for_evidence(
    claims: list[ReportClaim], evidence_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """找到引用指定证据的首条 claim，返回 (claim 文本, 命中的 citation)。"""
    for claim in claims:
        for cit in claim.get("citations") or []:
            if cit.get("evidence_id") == evidence_id:
                return (claim.get("text") or "").strip() or None, cit
    return None, None


def _side_view(
    claims: list[ReportClaim],
    evidence_index: dict[str, EvidenceDict],
    evidence_id: str,
) -> tuple[str, str]:
    """渲染冲突一方的「口径文本 + 来源链接」。

    口径文本优先取引用该证据的 report_claim 文本（reject 场景 claim 已被
    critic 移除），回退到证据 snippet/title；来源优先取 claim citation，
    回退到证据自带 title/url。
    """
    claim_text, citation = _claim_for_evidence(claims, evidence_id)
    if claim_text is not None and citation is not None:
        link = _source_link(citation.get("title"), citation.get("url"))
        return claim_text, link
    evidence = evidence_index.get(evidence_id)
    if evidence is not None:
        text = (evidence.get("snippet") or evidence.get("title") or "").strip()
        return text or "（该侧证据无文本摘要）", _source_link(evidence.get("title"), evidence.get("url"))
    # 证据行缺失（异常数据）：保留 ID 线索，不渲染为空白
    return f"（缺失证据 {evidence_id} 的文本）", f"证据 {evidence_id}"


def _conflict_meta_labels(conflict: ConflictDict) -> str:
    """渲染冲突条目后的类型/严重度中文括注。"""
    type_label = _CONFLICT_TYPE_LABELS.get(conflict.get("type") or "", "冲突")
    severity_label = _SEVERITY_LABELS.get(conflict.get("severity") or "", conflict.get("severity") or "")
    return f"{type_label}，严重度：{severity_label}"


def _render_conflict_block(
    claims: list[ReportClaim],
    evidence_index: dict[str, EvidenceDict],
    conflict: ConflictDict,
    *,
    verdict: VerdictDict | None,
) -> list[str]:
    """渲染单条冲突的议题 + 双方口径来源（+ 裁决信息），返回 Markdown 行。"""
    text_a, link_a = _side_view(claims, evidence_index, conflict["evidence_a_id"])
    text_b, link_b = _side_view(claims, evidence_index, conflict["evidence_b_id"])
    lines = [
        f"- **议题：{conflict.get('claim') or ''}**（{_conflict_meta_labels(conflict)}）",
        f"  - 口径 A：{text_a}",
        f"    - 来源：{link_a}",
        f"  - 口径 B：{text_b}",
        f"    - 来源：{link_b}",
    ]
    if verdict is not None:
        choice_label = _CHOICE_LABELS.get(verdict.get("choice") or "", verdict.get("choice") or "")
        lines.append(f"  - 裁决意见：{choice_label}")
        reason = (verdict.get("reason") or "").strip()
        if reason:
            lines.append(f"  - 裁决理由：{reason}")
        note = (verdict.get("additional_note") or "").strip()
        if note:
            lines.append(f"  - 局限备注：{note}")
    return lines


def _render_conflicts(section: dict[str, str], state: ResearchState) -> str:
    """冲突段：待裁决冲突 + 裁决后仍保留的未消解分歧（both/reject）。

    - 无冲突：一句无冲突说明；
    - low/medium 自动收敛（status=resolved 且无 verdict）与取一边的人工裁决
      只计入摘要计数，不渲染为待办（TR-5.1 / AC-16）；
    - awaiting_human/detected 且无 verdict：进「待人工裁决」区块；
    - verdict.choice 为 both/reject：进「分歧与局限」区块，含议题、双方
      口径与来源、裁决理由与 additional_note。
    """
    lines = [f"## {section['title']}", ""]
    conflicts: list[ConflictDict] = list(state.get("conflicts") or [])
    verdicts: list[VerdictDict] = list(state.get("verdicts") or [])
    if not conflicts:
        lines.append("研究期间未检测到重大冲突。")
        return "\n".join(lines) + "\n"

    verdict_by_id: dict[str, VerdictDict] = {v["conflict_id"]: v for v in verdicts}
    claims: list[ReportClaim] = list(state.get("report_claims") or [])
    evidence_index = _build_evidence_index(state)

    pending: list[ConflictDict] = []
    divergences: list[tuple[ConflictDict, VerdictDict]] = []
    n_auto_resolved = 0
    n_single_side = 0
    for conflict in conflicts:
        verdict = verdict_by_id.get(conflict["id"])
        if verdict is not None:
            if verdict.get("choice") in _DIVERGENCE_CHOICES:
                divergences.append((conflict, verdict))
            else:
                n_single_side += 1
        elif conflict.get("status") in _PENDING_STATUSES:
            pending.append(conflict)
        elif conflict.get("status") == "resolved":
            n_auto_resolved += 1

    # 收敛情况摘要（不逐条渲染已收敛冲突）
    summary_parts: list[str] = []
    if n_auto_resolved:
        summary_parts.append(f"{n_auto_resolved} 条由系统依据来源权威性自动收敛")
    if n_single_side:
        summary_parts.append(f"{n_single_side} 条经人工裁决采纳单一口径后收敛")
    if summary_parts:
        lines.append(f"本研究共检测到 {len(conflicts)} 条冲突：{'；'.join(summary_parts)}。")
        lines.append("")

    if pending:
        lines.append("### 待人工裁决")
        lines.append("")
        for conflict in pending:
            lines.extend(_render_conflict_block(claims, evidence_index, conflict, verdict=None))
        lines.append("")

    if divergences:
        lines.append("### 分歧与局限")
        lines.append("")
        lines.append("以下分歧经裁决后仍作为不确定性保留，结论段不将任一方口径写为定论：")
        lines.append("")
        for conflict, verdict in divergences:
            lines.extend(_render_conflict_block(claims, evidence_index, conflict, verdict=verdict))
        lines.append("")

    if not pending and not divergences:
        lines.append("所有冲突均已收敛，未保留未消解分歧。")
    return "\n".join(lines).rstrip() + "\n"


def _render_conclusion(section: dict[str, str], state: ResearchState) -> str:
    """结论段：基于已收敛 claim 数与分歧数给出简短总结。

    结论段只做计数与指引，不回写任何 claim 文本——被舍弃 claim 在 critic
    回流时已从 report_claims 移除，因此结构上不可能夹带（AC-16/TR-6.1）。
    """
    claims = state.get("report_claims") or []
    conflicts = state.get("conflicts") or []
    verdict_by_cid = {v["conflict_id"]: v for v in (state.get("verdicts") or [])}
    n_pending = sum(
        1 for c in conflicts if c["id"] not in verdict_by_cid and c.get("status") in _PENDING_STATUSES
    )
    n_divergence = sum(
        1
        for c in conflicts
        if (verdict := verdict_by_cid.get(c["id"])) is not None
        and verdict.get("choice") in _DIVERGENCE_CHOICES
    )
    lines = [
        f"## {section['title']}",
        "",
        f"综合上述 {len(claims)} 条核心发现，",
    ]
    tail = "建议结合各证据来源（见引用）进一步核查后再下决策。"
    if n_pending:
        lines.append(f"另有 {n_pending} 条待处理冲突尚待人工裁决；")
    if n_divergence:
        lines.append(
            f"其中 {n_divergence} 条分歧经裁决后作为不确定性保留"
            "（见「冲突与不确定性」段），相关结论不应被视为单一事实定论；"
        )
    lines.append(tail)
    return "\n".join(lines) + "\n"


def render_section(
    section: dict[str, str],
    state: ResearchState,
    conflicts: list[ConflictDict] | None = None,
    verdicts: list[VerdictDict] | None = None,
) -> str:
    """按 section.type 路由到具体渲染器。

    conflicts/verdicts 入参仅为兼容 M1 直接调用方保留；M2-2 起冲突段与
    结论段统一从 state 读取（需要 report_claims/standardized_evidence 联合渲染）。
    """
    del conflicts, verdicts
    stype = section.get("type") or ""
    if stype == OUTLINE_TYPE_BACKGROUND:
        return _render_background(section, state)
    if stype == OUTLINE_TYPE_FINDINGS:
        return _render_findings(section, state)
    if stype == OUTLINE_TYPE_CONFLICTS:
        return _render_conflicts(section, state)
    if stype == OUTLINE_TYPE_CONCLUSION:
        return _render_conclusion(section, state)
    # 未知类型 → 仅标题占位
    return f"## {section.get('title', section.get('id', 'section'))}\n\n（暂未实现该类型段落）\n"


# ---------------------------------------------------------------------------
# 节点入口
# ---------------------------------------------------------------------------


def _iter_claims(claims: Iterable[ReportClaim]) -> Iterable[ReportClaim]:
    """占位：M2+ 接入 LLM 后此处可改为按 section 过滤 claim 子集。"""
    return claims


@instrument(ResearchStage.REPORT)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """报告生成节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中。

    M1 简化：
    - 不调 LLM（纯模板拼接）；
    - 不持久化 Report ORM（state 内累积，下游由外层编排写入数据库）；
    - 不发 event_bus 事件（WP-6 才接）。
    """
    # 兼容 M0 签名：未注入 deps 时仍可运行
    del deps

    # 若 outline 已存在 → 沿用；否则按模板生成
    outline: list[dict[str, str]] = list(state.get("report_outline") or []) or default_outline(
        state.get("template_id") or DEFAULT_TEMPLATE_ID
    )

    # 遍历 outline 累积 Markdown；冲突/裁决数据统一由各渲染器从 state 读取
    chunks: list[str] = []
    for section in outline:
        # M2+ 可在此按 section.id 过滤 claim 子集传给 render_section
        _ = _iter_claims(state.get("report_claims") or [])
        chunks.append(render_section(section, state))

    report_draft: str = "\n".join(chunks)
    return {
        "report_outline": outline,
        "report_draft": report_draft,
    }


__all__ = [
    "run",
    "default_outline",
    "render_section",
    "OUTLINE_TYPE_BACKGROUND",
    "OUTLINE_TYPE_FINDINGS",
    "OUTLINE_TYPE_CONFLICTS",
    "OUTLINE_TYPE_CONCLUSION",
]
