"""结构化报告 blocks 绑定引擎（M2-7，纯函数、零 IO）。

引擎把 Reporter LLM 产出的区块草稿（或 LLM 不可用时的机械映射草稿）绑定为
前端可直接渲染的终稿 blocks，职责见技术方案 §5：

1. evidence_id 白名单过滤（幻觉引用剔除计数）与块内去重；
2. marker 报告级分配（按材料池顺序，仅被引证据占号，跨块共享）；
3. snippet 一律后端取自证据原文：quote 正文子串命中取原句窗口，否则回退
   Evidence.snippet（§5.4）；
4. 缺源降级（inferred）与含数字断言无引用块的一次自修复后剔除（§5.5）；
5. dispute 块确定性注入（§5.6，复用 reporting.disputes 原语）；
6. block/claim id、outline 派生、citation_audit 与引文关系行展开。

引擎不持有数据库会话：trafilatura 正文经 ``content_map`` 入参注入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.orchestrator.state import ConflictDict, EvidenceDict, ReportClaim, VerdictDict
from app.reporting import disputes
from app.reporting.schemas import LLMReportPlan

# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------

# 三类 LLM 可产区块（dispute 只能由引擎注入）
_DRAFT_TYPES = frozenset({"conclusion", "evidence", "limitation"})
_CONFIDENCE_VALUES = frozenset({"single_source", "cross_verified", "inferred"})

# 论断/引用区块类型
_CLAIM_TYPES = frozenset({"conclusion", "dispute"})


@dataclass(slots=True, frozen=True)
class EvidenceText:
    """引擎取 snippet 所需的证据文本（节点查库后组装）。"""

    snippet: str
    content: str | None = None


@dataclass(slots=True)
class DraftBlock:
    """进入引擎的区块草稿（LLM 草稿与机械映射共用）。"""

    type: str
    text: str
    confidence: str
    evidence_ids: list[str] = field(default_factory=list)
    quotes: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ReportAssembly:
    """引擎终稿产物：blocks/outline 直接入 content_json，citation_rows 待挂 report_id。"""

    blocks: list[dict[str, Any]]
    outline: list[dict[str, Any]]
    audit: dict[str, Any]
    # 行形态：{block_id, evidence_id, claim_id, position, snippet}
    citation_rows: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# outline / 兜底文本常量（§5.8）
# ---------------------------------------------------------------------------

_OUTLINE_OVERVIEW = {"id": "sec-overview", "title": "调研概述", "type": "conclusion"}
_OUTLINE_FINDINGS = {"id": "sec-findings", "title": "核心发现", "type": "conclusion"}
_OUTLINE_DISPUTES = {"id": "sec-disputes", "title": "分歧与不确定性", "type": "dispute"}
_OUTLINE_LIMITATIONS = {"id": "sec-limitations", "title": "局限与建议", "type": "limitation"}

# LLM 完全没产出局限块时的引擎兜底（保证 A3 局限区块不缺位）
_FALLBACK_LIMITATION_TEXT = (
    "本报告基于公开检索到的有限材料形成：部分信源可能存在发布时间缺失或未经第三方"
    "独立复核的情形；报告中的数字与事实均可经角标回溯至原始来源，建议在据此决策前"
    "结合最新官方口径再行复核。"
)

# ---------------------------------------------------------------------------
# 数字断言模式（§5.5）
# ---------------------------------------------------------------------------

# 命中即视为「含数字或事实数据点」：
# - 阿拉伯数字（含日期/百分比/小数）、百分号、货币符号；
# - 排名序数「第 N」；中文数量（连续两个及以上数词，或数词+倍/万/亿/%）。
_NUMERIC_ASSERTION_RE = re.compile(
    r"\d"
    r"|[％%]"
    r"|[$￥€£]"
    r"|第[零一二两三四五六七八九十百千0-9]+"
    r"|[零一二两三四五六七八九十百千万亿]{2,}"
    r"|[零一二两三四五六七八九十百千万亿]\s*(?:倍|万|亿|％|%)"
)

# quote 子串校验的最小长度（过短的引号不做命中判定，避免标点级误匹配）
_QUOTE_MIN_LEN = 6
# 原句窗口的最大字符数
_QUOTE_WINDOW_LIMIT = 120
# 向两侧扩展句子边界的最大搜索距离
_SENTENCE_EXPAND = 60
_SENTENCE_PUNCT = "。！？；;\n"


def has_numeric_assertion(text: str) -> bool:
    """文本是否含数字/事实数据点断言（§5.5 审计口径）。"""
    return bool(_NUMERIC_ASSERTION_RE.search(text or ""))


def find_numeric_violations(drafts: list[DraftBlock], pool_ids: set[str]) -> list[int]:
    """返回含数字断言但无有效引用的 conclusion 草稿下标（供节点触发一次自修复）。"""
    violations: list[int] = []
    for idx, draft in enumerate(drafts):
        if draft.type != "conclusion":
            continue
        valid = [eid for eid in draft.evidence_ids if eid in pool_ids]
        if not valid and has_numeric_assertion(draft.text):
            violations.append(idx)
    return violations


def drafts_from_plan(plan: LLMReportPlan) -> list[DraftBlock]:
    """把通过 Pydantic 校验的 LLM 计划转为引擎草稿；空白文本块直接剔除。"""
    drafts: list[DraftBlock] = []
    for item in plan.blocks:
        text = item.text.strip()
        if not text:
            continue
        drafts.append(
            DraftBlock(
                type=item.type,
                text=text,
                confidence=item.confidence,
                evidence_ids=list(item.evidence_ids),
                quotes={k: v for k, v in item.quotes.items() if isinstance(v, str)},
            )
        )
    return drafts


def mechanical_drafts(claims: list[ReportClaim], pool_ids: set[str]) -> list[DraftBlock]:
    """LLM 不可用时的机械映射：每条收敛 claim 转一个 conclusion 草稿（§5.7）。"""
    drafts: list[DraftBlock] = []
    for claim in claims:
        text = (claim.get("text") or "").strip()
        if not text:
            continue
        evidence_ids: list[str] = []
        for cit in claim.get("citations") or []:
            ev_id = cit.get("evidence_id")
            if isinstance(ev_id, str) and ev_id in pool_ids and ev_id not in evidence_ids:
                evidence_ids.append(ev_id)
        confidence = claim.get("confidence") or "single_source"
        drafts.append(
            DraftBlock(
                type="conclusion",
                text=text,
                confidence=confidence if confidence in _CONFIDENCE_VALUES else "single_source",
                evidence_ids=evidence_ids,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# quote 原文匹配与 snippet 窗口（§5.4）
# ---------------------------------------------------------------------------


def _strip_with_map(text: str) -> tuple[str, list[int]]:
    """删除全部空白字符，返回（紧凑文本, 紧凑位→原始下标映射）。"""
    chars: list[str] = []
    offsets: list[int] = []
    for idx, ch in enumerate(text):
        if ch.isspace():
            continue
        chars.append(ch)
        offsets.append(idx)
    return "".join(chars), offsets


def _window_span(content: str, start: int, end: int) -> str:
    """以命中区间为中心向句子边界扩展，硬上限 120 字。"""
    left = start
    bound = max(0, start - _SENTENCE_EXPAND)
    while left > bound and content[left - 1] not in _SENTENCE_PUNCT:
        left -= 1
    right = end
    upper = min(len(content), end + _SENTENCE_EXPAND)
    while right < upper and content[right] not in _SENTENCE_PUNCT:
        right += 1
    if right < len(content) and content[right] in _SENTENCE_PUNCT:
        right += 1
    snippet = content[left:right].strip()
    if len(snippet) > _QUOTE_WINDOW_LIMIT:
        # 区间本身超长：以命中点为中心截取 120 字
        center = (start + end) // 2
        half = _QUOTE_WINDOW_LIMIT // 2
        snippet = content[max(0, center - half) : center + half].strip()
    return snippet


def resolve_quote_snippet(content: str, quote: str) -> str | None:
    """quote 在正文中命中则返回原句窗口；直接命中与规范化（去空白）命中均认可。"""
    quote = quote.strip()
    if len(quote) < _QUOTE_MIN_LEN:
        return None
    direct = content.find(quote)
    if direct >= 0:
        return _window_span(content, direct, direct + len(quote))
    stripped_content, offsets = _strip_with_map(content)
    stripped_quote = re.sub(r"\s+", "", quote)
    if len(stripped_quote) < _QUOTE_MIN_LEN:
        return None
    pos = stripped_content.find(stripped_quote)
    if pos < 0:
        return None
    start = offsets[pos]
    end = offsets[pos + len(stripped_quote) - 1] + 1
    return _window_span(content, start, end)


# ---------------------------------------------------------------------------
# 引擎主入口
# ---------------------------------------------------------------------------


def _dedupe_preserve(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for ev_id in ids:
        if ev_id not in seen:
            seen.add(ev_id)
            ordered.append(ev_id)
    return ordered


def _fallback_snippet(
    evidence_id: str,
    *,
    material_index: dict[str, EvidenceDict],
    content_map: dict[str, EvidenceText],
) -> str:
    """quote 未命中时的摘要级片段：优先 content_map 行内 snippet，回退状态证据。"""
    row = content_map.get(evidence_id)
    if row is not None and row.snippet:
        return row.snippet
    evidence = material_index.get(evidence_id)
    if evidence is not None:
        return evidence.get("snippet") or evidence.get("title") or ""
    return ""


def _dispute_entries(
    *,
    claims: list[ReportClaim],
    conflicts: list[ConflictDict],
    verdicts: list[VerdictDict],
    evidence_index: dict[str, EvidenceDict],
    pool_ids: set[str],
) -> tuple[list[dict[str, Any]], int]:
    """构造 dispute 中间块（带 conflict_id，不带 quote）。

    双方证据均被剔除的冲突不产出块、计入 skipped（§5.6）。
    """
    entries: list[dict[str, Any]] = []
    skipped = 0
    for conflict, verdict in disputes.select_disputes(conflicts, verdicts):
        survivors = _dedupe_preserve(
            [eid for eid in (conflict["evidence_a_id"], conflict["evidence_b_id"]) if eid in pool_ids]
        )
        if not survivors:
            # 双方证据均被剔除：无任何有效溯源载体，不产出论断块（§5.6）
            skipped += 1
            continue
        text = disputes.compose_dispute_text(
            conflict,
            verdict,
            claims=claims,
            evidence_index=evidence_index,
            survivor_ids=set(survivors),
        )
        confidence = "cross_verified" if len(survivors) >= 2 else "single_source"
        entries.append(
            {
                "type": "dispute",
                "text": text,
                "confidence": confidence,
                "citation_ids": survivors,
                "quotes": {},
                "conflict_id": conflict["id"],
            }
        )
    return entries, skipped


def _fallback_overview_block(question: str) -> dict[str, Any]:
    """无任何结论块时的概述块（机械降级无 claim 场景，结构不降级）。

    该块零引用且标 inferred，必须与 §5.5 同口径：研究问题含数字断言（年份/
    百分比/数量词等）时不得把问题原文嵌进来，否则终稿会出现无来源数字论断
    （联调 P1-1 根因）；此时改用不含任何数字断言的通用概述。
    """
    q = (question or "").strip()
    if q and not has_numeric_assertion(q):
        text = f"围绕「{q}」开展的多源核验已形成以下结构化报告。"
    else:
        text = "本次研究的多源核验结果如下。"
    return {"type": "conclusion", "text": text, "confidence": "inferred", "citation_ids": []}


def _fallback_limitation_draft() -> DraftBlock:
    return DraftBlock(type="limitation", text=_FALLBACK_LIMITATION_TEXT, confidence="inferred")


def assemble_report(
    *,
    drafts: list[DraftBlock],
    material: list[EvidenceDict],
    claims: list[ReportClaim],
    conflicts: list[ConflictDict],
    verdicts: list[VerdictDict],
    content_map: dict[str, EvidenceText],
    question: str = "",
) -> ReportAssembly:
    """把区块草稿绑定为终稿 blocks/outline/audit/引文行（详见模块文档串）。"""
    material_index = {ev["id"]: ev for ev in material}
    pool_ids = set(material_index)
    evidence_index = disputes.build_evidence_index(material)

    # ---- 1. 白名单过滤、块内去重、幻觉计数；缺源降级/数字违例剔除（§5.5） ----
    hallucinated_refs = 0
    dropped_numeric_blocks = 0
    forced_inferred_blocks = 0
    kept: list[dict[str, Any]] = []
    for draft in drafts:
        if draft.type not in _DRAFT_TYPES:
            # 理论上 LLM schema 已禁止 dispute；防御性丢弃未知类型
            continue
        valid_ids = _dedupe_preserve([eid for eid in draft.evidence_ids if eid in pool_ids])
        hallucinated_refs += len(draft.evidence_ids) - len(valid_ids)
        if draft.type == "conclusion" and not valid_ids:
            if has_numeric_assertion(draft.text):
                # 自修复后仍无引用的数字断言块：剔除，不冒充事实
                dropped_numeric_blocks += 1
                continue
            draft.confidence = "inferred"
            forced_inferred_blocks += 1
        confidence = draft.confidence if draft.confidence in _CONFIDENCE_VALUES else "single_source"
        kept.append(
            {
                "type": draft.type,
                "text": draft.text,
                "confidence": confidence,
                "citation_ids": valid_ids,
                "quotes": draft.quotes,
            }
        )

    # ---- 2. dispute 确定性注入（§5.6） ----
    dispute_blocks, skipped_disputes = _dispute_entries(
        claims=claims,
        conflicts=conflicts,
        verdicts=verdicts,
        evidence_index=evidence_index,
        pool_ids=pool_ids,
    )

    # ---- 3. 排序：LLM 块顺序保留，dispute 插在首个 limitation 之前（§5.8） ----
    ordered: list[dict[str, Any]] = []
    inserted = False
    for block in kept:
        if block["type"] == "limitation" and not inserted:
            ordered.extend(dispute_blocks)
            inserted = True
        ordered.append(block)
    if not inserted:
        ordered.extend(dispute_blocks)

    # 无结论块（机械降级且无 claim 等）：概述块置顶，保证概述/发现章有实体
    if not any(b["type"] == "conclusion" for b in ordered):
        ordered.insert(0, _fallback_overview_block(question))
        forced_inferred_blocks += 1
    # 无局限块：引擎兜底
    if not any(b["type"] == "limitation" for b in ordered):
        fb = _fallback_limitation_draft()
        ordered.append(
            {
                "type": "limitation",
                "text": fb.text,
                "confidence": "inferred",
                "citation_ids": [],
                "quotes": {},
            }
        )

    # ---- 4. marker 分配：材料池顺序 ∩ 被引集合，仅被引占号（§5.3） ----
    referenced: set[str] = set()
    for block in ordered:
        referenced.update(block["citation_ids"])
    marker_number: dict[str, int] = {}
    n = 0
    for ev in material:
        if ev["id"] in referenced:
            n += 1
            marker_number[ev["id"]] = n

    # ---- 5. id/claim_id 分配与 snippet 填充 ----
    final_blocks: list[dict[str, Any]] = []
    citation_rows: list[dict[str, Any]] = []
    bound_blocks = 0
    claim_blocks = 0
    quote_verified_refs = 0
    claim_seq = 0
    for block_idx, block in enumerate(ordered, start=1):
        block_id = f"block-{block_idx:02d}"
        is_claim_block = block["type"] in _CLAIM_TYPES
        claim_id: str | None = None
        if is_claim_block:
            claim_seq += 1
            claim_id = f"claim-{claim_seq:02d}"
            claim_blocks += 1
            if block["citation_ids"]:
                bound_blocks += 1

        citations: list[dict[str, Any]] = []
        for evidence_id in block["citation_ids"]:
            quote = (block.get("quotes") or {}).get(evidence_id) or ""
            verified = False
            snippet = ""
            row = content_map.get(evidence_id)
            if quote and row is not None and row.content:
                window = resolve_quote_snippet(row.content, quote)
                if window is not None:
                    snippet = window
                    verified = True
            if not snippet:
                snippet = _fallback_snippet(
                    evidence_id, material_index=material_index, content_map=content_map
                )
            if verified:
                quote_verified_refs += 1
            citations.append(
                {"evidence_id": evidence_id, "marker": f"[{marker_number[evidence_id]}]", "snippet": snippet}
            )
            citation_rows.append(
                {
                    "block_id": block_id,
                    "evidence_id": evidence_id,
                    "claim_id": claim_id,
                    "position": marker_number[evidence_id],
                    "snippet": snippet,
                }
            )

        final_block: dict[str, Any] = {"id": block_id, "type": block["type"], "text": block["text"]}
        if claim_id is not None:
            final_block["claim_id"] = claim_id
        if is_claim_block:
            final_block["confidence"] = block["confidence"]
        if block.get("conflict_id"):
            final_block["conflict_id"] = block["conflict_id"]
        final_block["citations"] = citations
        final_blocks.append(final_block)

    # ---- 6. outline 派生（语义 id，分歧/局限章按需出现） ----
    outline: list[dict[str, Any]] = [dict(_OUTLINE_OVERVIEW), dict(_OUTLINE_FINDINGS)]
    if any(b["type"] == "dispute" for b in final_blocks):
        outline.append(dict(_OUTLINE_DISPUTES))
    outline.append(dict(_OUTLINE_LIMITATIONS))

    # ---- 7. 数字绑定率按终稿实际回算（可自检，不信任上游计数；§5.5 恒等式） ----
    numeric_claim_total = 0
    numeric_claim_bound = 0
    for block in final_blocks:
        if block["type"] in _CLAIM_TYPES and has_numeric_assertion(block["text"]):
            numeric_claim_total += 1
            if block["citations"]:
                numeric_claim_bound += 1
    numeric_binding_rate = round(numeric_claim_bound / numeric_claim_total, 3) if numeric_claim_total else 1.0

    audit = {
        "claim_blocks": claim_blocks,
        "bound_blocks": bound_blocks,
        "forced_inferred_blocks": forced_inferred_blocks,
        "dropped_numeric_blocks": dropped_numeric_blocks,
        "skipped_disputes": skipped_disputes,
        "hallucinated_refs": hallucinated_refs,
        "quote_verified_refs": quote_verified_refs,
        "numeric_claim_binding_rate": numeric_binding_rate,
    }
    return ReportAssembly(
        blocks=final_blocks,
        outline=outline,
        audit=audit,
        citation_rows=citation_rows,
    )


__all__ = [
    "DraftBlock",
    "EvidenceText",
    "ReportAssembly",
    "assemble_report",
    "drafts_from_plan",
    "find_numeric_violations",
    "has_numeric_assertion",
    "mechanical_drafts",
    "resolve_quote_snippet",
]
