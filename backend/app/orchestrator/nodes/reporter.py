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

import asyncio
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.evidence import Evidence
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
from app.provider.base import ChatMessage
from app.reporting import blocks as report_engine
from app.reporting import disputes
from app.reporting.prompts import build_messages, build_repair_user_message
from app.reporting.schemas import LLMReportPlan

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

# 冲突/裁决中文标签与收敛状态机：M2-7 起统一由 reporting.disputes 提供，
# Markdown 渲染与结构化 blocks 引擎共用同一份原语
_CONFLICT_TYPE_LABELS = disputes.CONFLICT_TYPE_LABELS
_SEVERITY_LABELS = disputes.SEVERITY_LABELS
_CHOICE_LABELS = disputes.CHOICE_LABELS
# choice 取一边即视为已收敛（冲突段不逐条展开）；以下两值属于「裁决后仍保留的
# 未消解分歧」，必须进入「分歧与局限」区块显式呈现（AC-7/AC-16）
_DIVERGENCE_CHOICES = disputes.DIVERGENCE_CHOICES
# 未挂起（无 verdict）即视为待人工裁决的冲突状态
_PENDING_STATUSES = disputes.PENDING_STATUSES


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
    return disputes.build_evidence_index(
        list(state.get("standardized_evidence") or []),
        list(state.get("evidence") or []),
    )


def _claim_for_evidence(
    claims: list[ReportClaim], evidence_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """找到引用指定证据的首条 claim，返回 (claim 文本, 命中的 citation)。"""
    return disputes.claim_for_evidence(claims, evidence_id)


def _side_view(
    claims: list[ReportClaim],
    evidence_index: dict[str, EvidenceDict],
    evidence_id: str,
) -> tuple[str, str]:
    """渲染冲突一方的「口径文本 + 来源链接」（文本原语在 reporting.disputes）。"""
    text, title = disputes.evidence_side(claims, evidence_index, evidence_id)
    evidence = evidence_index.get(evidence_id)
    url = evidence.get("url") if evidence is not None else None
    return text, _source_link(title, url)


def _conflict_meta_labels(conflict: ConflictDict) -> str:
    """渲染冲突条目后的类型/严重度中文括注。"""
    return disputes.conflict_meta_labels(conflict)


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
# M2-7：结构化终稿（LLM blocks 计划 + 确定性绑定引擎）
# ---------------------------------------------------------------------------


def _limitation_signals(
    material: list[EvidenceDict],
    conflicts: list[ConflictDict],
    verdicts: list[VerdictDict],
    *,
    excluded_count: int,
) -> dict[str, Any]:
    """汇总喂给 LLM 的局限信号（分歧要点/剔除/元数据缺失计数）。"""
    verdict_by_id = {v["conflict_id"]: v for v in verdicts}
    pending = 0
    divergence: list[dict[str, Any]] = []
    for conflict in conflicts:
        verdict = verdict_by_id.get(conflict["id"])
        if verdict is not None:
            if verdict.get("choice") in disputes.DIVERGENCE_CHOICES:
                divergence.append({"claim": conflict.get("claim"), "note": verdict.get("additional_note")})
        elif conflict.get("status") in disputes.PENDING_STATUSES:
            pending += 1
    return {
        "pending_conflicts": pending,
        "divergence_conflicts": divergence,
        "excluded_evidence_count": excluded_count,
        "missing_published_at_count": sum(1 for ev in material if not ev.get("published_at")),
        "d_level_evidence_count": sum(1 for ev in material if ev.get("credibility") == "D"),
    }


async def _load_content_map(
    db_session: Any,
    run_id: str,
) -> dict[str, report_engine.EvidenceText]:
    """按 run 查 Evidence 行组装 content_map（正文不入 state，§5.4）。

    统一走 ``scalars(select(实体))`` 与项目持久化层/假会话约定一致；调用方按
    材料池 id 取行，池外证据不进入引擎。
    """
    rows = (await db_session.scalars(select(Evidence).where(Evidence.run_id == run_id))).all()
    return {
        row.id: report_engine.EvidenceText(snippet=row.snippet or "", content=row.content) for row in rows
    }


def _state_content_map(material: list[EvidenceDict]) -> dict[str, report_engine.EvidenceText]:
    """db_session 缺省路径（测试/未配置）：仅有摘要，正文留空走 snippet 回退。"""
    return {
        ev["id"]: report_engine.EvidenceText(snippet=ev.get("snippet") or "", content=None) for ev in material
    }


async def _call_plan(
    llm: Any,
    messages: list[ChatMessage],
) -> tuple[list[report_engine.DraftBlock], int]:
    """单次结构化调用并转为引擎草稿；脏返回/空计划抛异常由调用方降级。"""
    settings = get_settings()
    completion = await asyncio.wait_for(
        llm.complete_structured(
            messages=messages,
            schema=LLMReportPlan,
            temperature=0.0,
            max_tokens=settings.report_llm_max_tokens,
            tags=["report"],
        ),
        timeout=settings.report_llm_timeout_seconds,
    )
    parsed = completion.parsed
    if not isinstance(parsed, LLMReportPlan):
        raise TypeError(f"reporter LLM 返回非预期类型：{type(parsed).__name__}")
    drafts = report_engine.drafts_from_plan(parsed)
    if not drafts:
        raise ValueError("reporter LLM 计划经清洗后无有效区块")
    return drafts, int(completion.usage.get("total_tokens", 0))


async def _build_structured_report(
    render_state: dict[str, Any],
    *,
    llm: Any,
    db_session: Any,
) -> tuple[report_engine.ReportAssembly, bool, int]:
    """生成结构化终稿；返回（装配产物, 是否降级, 消耗 token）。"""
    material: list[EvidenceDict] = list(render_state.get("standardized_evidence") or [])
    claims: list[ReportClaim] = list(render_state.get("report_claims") or [])
    conflicts: list[ConflictDict] = list(render_state.get("conflicts") or [])
    verdicts: list[VerdictDict] = list(render_state.get("verdicts") or [])
    pool_ids = {ev["id"] for ev in material}
    run_id = str(render_state.get("run_id") or "")

    drafts: list[report_engine.DraftBlock] | None = None
    consumed = 0
    if llm is not None and material:
        messages = build_messages(
            question=str(render_state.get("question") or ""),
            goal=_as_text((render_state.get("clarification") or {}).get("goal"))
            if isinstance(render_state.get("clarification"), dict)
            else "",
            scope=_as_text((render_state.get("clarification") or {}).get("scope"))
            if isinstance(render_state.get("clarification"), dict)
            else "",
            material=material,
            claims=claims,
            signals=_limitation_signals(
                material,
                conflicts,
                verdicts,
                excluded_count=len(render_state.get("_excluded_ids") or []),
            ),
        )
        try:
            drafts, consumed = await _call_plan(llm, messages)
            violations = report_engine.find_numeric_violations(drafts, pool_ids)
            if violations:
                # 含数字断言但无引用的块：带反馈整份自修复，仅一次（§5.5）
                repair_messages = [
                    messages[0],
                    ChatMessage(
                        role="user",
                        content=build_repair_user_message(
                            messages[1].content, [drafts[i].text for i in violations]
                        ),
                    ),
                ]
                try:
                    repaired, repair_tokens = await _call_plan(llm, repair_messages)
                    drafts, consumed = repaired, consumed + repair_tokens
                except Exception as exc:  # noqa: BLE001 - 自修复失败保留首版，引擎剔除违规块
                    log.warning(
                        "reporter 自修复调用失败，保留首版计划并由引擎剔除违规块",
                        extra={"run_id": run_id, "error": repr(exc)},
                    )
        except Exception as exc:  # noqa: BLE001 - 未配置/熔断/超时/脏 JSON 统一机械降级
            log.warning(
                "reporter LLM 结构化生成失败，降级机械映射", extra={"run_id": run_id, "error": repr(exc)}
            )
            drafts = None

    degraded = drafts is None
    if drafts is None:
        drafts = report_engine.mechanical_drafts(claims, pool_ids)

    if db_session is not None and material and run_id:
        content_map = await _load_content_map(db_session, run_id)
    else:
        content_map = _state_content_map(material)

    assembly = report_engine.assemble_report(
        drafts=drafts,
        material=material,
        claims=claims,
        conflicts=conflicts,
        verdicts=verdicts,
        content_map=content_map,
        question=str(render_state.get("question") or ""),
    )
    return assembly, degraded, consumed


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

    M2-7：保留 M1 的 Markdown 模板渲染（content_md/生成中预览），新增结构化
    终稿 ``report_blocks``——一次 LLM 结构化调用出 blocks 计划，经确定性绑定
    引擎补 marker/snippet/缺源降级/dispute 注入；LLM 不可用走机械映射降级并
    置 ``reporter_degraded``。节点不持久化 ORM，由 executor 统一落库。
    """
    # M2-5：剔除证据不进报告渲染（渲染视图过滤，不改写 checkpoint 中的 state）
    render_state: dict[str, Any] = dict(state)
    db_session = getattr(deps, "db_session", None) if deps is not None else None
    if db_session is not None:
        from app.services.interventions import excluded_evidence_ids

        excluded = await excluded_evidence_ids(db_session, str(state.get("run_id") or ""))
        if excluded:
            render_state["standardized_evidence"] = [
                ev for ev in (state.get("standardized_evidence") or []) if ev.get("id") not in excluded
            ]
            render_state["evidence"] = [
                ev for ev in (state.get("evidence") or []) if ev.get("id") not in excluded
            ]
        render_state["_excluded_ids"] = excluded

    # 若 outline 已存在 → 沿用；否则按模板生成（旧四段，仅供 Markdown 渲染）
    outline: list[dict[str, str]] = list(state.get("report_outline") or []) or default_outline(
        state.get("template_id") or DEFAULT_TEMPLATE_ID
    )

    # 遍历 outline 累积 Markdown；冲突/裁决数据统一由各渲染器从 state 读取
    chunks: list[str] = []
    for section in outline:
        # M2+ 可在此按 section.id 过滤 claim 子集传给 render_section
        _ = _iter_claims(render_state.get("report_claims") or [])
        chunks.append(render_section(section, render_state))  # type: ignore[arg-type]

    report_draft: str = "\n".join(chunks)

    # M2-7：结构化终稿（LLM + 绑定引擎），失败显式降级、结构恒完整
    llm = getattr(deps, "llm", None) if deps is not None else None
    assembly, degraded, report_tokens = await _build_structured_report(
        render_state, llm=llm, db_session=db_session
    )

    patch: dict[str, Any] = {
        "report_outline": outline,
        "report_draft": report_draft,
        "report_blocks": assembly.blocks,
        "reporter_degraded": degraded,
    }
    # outline/audit/引文行经 deps run 级通道交给 executor 落库（不占 state 字段）
    if deps is not None:
        deps.report_assembly = assembly
    if report_tokens:
        patch["token_used"] = int(state.get("token_used") or 0) + report_tokens
    return patch


__all__ = [
    "run",
    "default_outline",
    "render_section",
    "OUTLINE_TYPE_BACKGROUND",
    "OUTLINE_TYPE_FINDINGS",
    "OUTLINE_TYPE_CONFLICTS",
    "OUTLINE_TYPE_CONCLUSION",
]
