"""报告生成节点（§6.5.7 reporter）。

契约：
- 输入：``ResearchState`` 含 ``report_claims``（WP-5.6 产出）/
    ``conflicts`` / ``verdicts`` / ``question``；可选已有 ``report_outline``。
- 输出：合并到 state 的字段包括 ``report_outline`` / ``report_draft`` /
    ``current_stage`` / ``updated_at``。
- 行为：
    1. ``default_outline(template_id)`` —— 按模板 ID 返回分段大纲；
       M1 默认通用模板（4 段：背景 / 核心发现 / 冲突与不确定性 / 结论）。
    2. ``render_section`` —— M1 简化版：纯模板渲染，不调 LLM；
       按 section 类型拼接标题 + 关联 claim 列表 + 引用链接。
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
    ReportClaim,
    ResearchStage,
    ResearchState,
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


def _render_background(section: dict[str, str], state: ResearchState) -> str:
    """背景段：仅以研究问题 + 澄清目标开篇。"""
    question = state.get("question") or ""
    clarification = state.get("clarification") or {}
    goal = clarification.get("goal") if isinstance(clarification, dict) else None
    lines = [
        f"## {section['title']}",
        "",
        f"本研究围绕以下问题展开：**{question}**。",
    ]
    if goal:
        lines.append("")
        lines.append(f"- 研究目标：{goal}")
    scope = clarification.get("scope") if isinstance(clarification, dict) else None
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
        conf = c.get("confidence") or "single_source"
        lines.append(f"{i}. {text}（confidence: {conf}）")
    # 引用汇总
    lines.append("")
    lines.append("**引用：**")
    for i, c in enumerate(claims, start=1):
        for cit in c.get("citations") or []:
            lines.append(_format_citation(i, cit))
    return "\n".join(lines) + "\n"


def _render_conflicts(
    section: dict[str, str],
    conflicts: list[ConflictDict],
    verdicts: list[dict[str, Any]],
) -> str:
    """冲突段：未裁决冲突 + 已裁决说明。"""
    lines = [f"## {section['title']}", ""]
    if not conflicts:
        lines.append("研究期间未检测到重大冲突。")
        return "\n".join(lines) + "\n"
    # 已裁决 → 标记 resolved
    resolved_ids = {v.get("conflict_id") for v in verdicts or []}
    pending = [c for c in conflicts if c["id"] not in resolved_ids]
    if not pending:
        lines.append("所有冲突已由用户裁决。")
        return "\n".join(lines) + "\n"
    for c in pending:
        lines.append(
            f"- （severity={c.get('severity')}）{c.get('claim') or ''} "
            f"[evidence_a={c.get('evidence_a_id')} / evidence_b={c.get('evidence_b_id')}]"
        )
    return "\n".join(lines) + "\n"


def _render_conclusion(section: dict[str, str], state: ResearchState) -> str:
    """结论段：基于已收敛 claim 数 + 冲突数给出简短总结。"""
    claims = state.get("report_claims") or []
    conflicts = state.get("conflicts") or []
    n_claims = len(claims)
    n_conflicts = len(conflicts)
    lines = [
        f"## {section['title']}",
        "",
        f"综合上述 {n_claims} 条核心发现",
    ]
    if n_conflicts:
        lines.append(f"以及 {n_conflicts} 条待处理冲突，")
    else:
        lines[-1] += "，"
    lines.append("建议结合各证据来源（见引用）进一步核查后再下决策。")
    return "\n".join(lines) + "\n"


def render_section(
    section: dict[str, str],
    state: ResearchState,
    conflicts: list[ConflictDict],
    verdicts: list[dict[str, Any]],
) -> str:
    """按 section.type 路由到具体渲染器。"""
    stype = section.get("type") or ""
    if stype == OUTLINE_TYPE_BACKGROUND:
        return _render_background(section, state)
    if stype == OUTLINE_TYPE_FINDINGS:
        return _render_findings(section, state)
    if stype == OUTLINE_TYPE_CONFLICTS:
        return _render_conflicts(section, conflicts, verdicts)
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

    conflicts: list[ConflictDict] = list(state.get("conflicts") or [])
    verdicts: list[dict[str, Any]] = list(state.get("verdicts") or [])

    # 遍历 outline 累积 Markdown
    chunks: list[str] = []
    for section in outline:
        # M2+ 可在此按 section.id 过滤 claim 子集传给 render_section
        _ = _iter_claims(state.get("report_claims") or [])
        chunks.append(render_section(section, state, conflicts, verdicts))

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
