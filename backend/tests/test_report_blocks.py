"""M2-7 结构化报告绑定引擎单元测试（app.reporting.blocks）。

对应技术方案 AC-1~AC-8 的纯函数分支；落库/API/真库在 T4/T5 用例覆盖。
离线零网络：content_map 由用例手工构造，不查库不调 LLM。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.state import ConflictDict, EvidenceDict, ReportClaim, VerdictDict
from app.reporting.blocks import (
    DraftBlock,
    EvidenceText,
    assemble_report,
    drafts_from_plan,
    find_numeric_violations,
    has_numeric_assertion,
    mechanical_drafts,
)
from app.reporting.schemas import LLMBlockDraftModel, LLMReportPlan

# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def _evidence(ev_id: str, *, snippet: str = "摘要", title: str = "标题") -> EvidenceDict:
    return {
        "id": ev_id,
        "sub_question_id": "sq1",
        "url": f"https://example.com/{ev_id}",
        "domain": "example.com",
        "title": title,
        "snippet": snippet,
        "source_type": "news",
        "source_level": "secondary",
        "credibility": "B",
        "fingerprint": f"fp-{ev_id}",
        "published_at": None,
        "fetched_at": "2026-09-01T00:00:00+00:00",
    }


def _material(*ids: str) -> list[EvidenceDict]:
    return [_evidence(eid, snippet=f"证据 {eid} 摘要", title=f"来源 {eid}") for eid in ids]


def _conflict(
    cid: str = "c1",
    *,
    a: str = "ea",
    b: str = "eb",
    status: str = "awaiting_human",
    severity: str = "high",
    claim_text: str = "对立议题",
) -> ConflictDict:
    return {
        "id": cid,
        "claim": claim_text,
        "evidence_a_id": a,
        "evidence_b_id": b,
        "type": "perspective",
        "severity": severity,
        "status": status,  # type: ignore[typeddict-item]
    }


def _verdict(cid: str, choice: str, *, note: str | None = None) -> VerdictDict:
    v: VerdictDict = {"conflict_id": cid, "user_id": "u1", "choice": choice, "reason": "裁决理由"}
    if note is not None:
        v["additional_note"] = note
    return v


def _claim(cid: str, text: str, *ev_ids: str, confidence: str = "single_source") -> ReportClaim:
    return {
        "id": cid,
        "text": text,
        "confidence": confidence,  # type: ignore[typeddict-item]
        "citations": [
            {
                "evidence_id": eid,
                "url": f"https://example.com/{eid}",
                "title": f"来源 {eid}",
                "snippet": f"证据 {eid} 摘要",
            }
            for eid in ev_ids
        ],
    }


def _draft(
    btype: str = "conclusion",
    text: str = "一条综合结论",
    *,
    confidence: str = "cross_verified",
    evidence_ids: list[str] | None = None,
    quotes: dict[str, str] | None = None,
) -> DraftBlock:
    return DraftBlock(
        type=btype,
        text=text,
        confidence=confidence,
        evidence_ids=evidence_ids or [],
        quotes=quotes or {},
    )


def _assemble(
    drafts: list[DraftBlock],
    *,
    material: list[EvidenceDict] | None = None,
    claims: list[ReportClaim] | None = None,
    conflicts: list[ConflictDict] | None = None,
    verdicts: list[VerdictDict] | None = None,
    content_map: dict[str, EvidenceText] | None = None,
    question: str = "研究问题",
):
    material = material if material is not None else _material("ea", "eb")
    return assemble_report(
        drafts=drafts,
        material=material,
        claims=claims or [],
        conflicts=conflicts or [],
        verdicts=verdicts or [],
        content_map=content_map or {},
        question=question,
    )


def _blocks_by_type(assembly, btype: str) -> list[dict[str, Any]]:
    return [b for b in assembly.blocks if b["type"] == btype]


# ---------------------------------------------------------------------------
# 数字断言模式
# ---------------------------------------------------------------------------


class TestNumericAssertion:
    @pytest.mark.parametrize(
        "text",
        [
            "P99 延迟低于 80ms",
            "同比增长 30%",
            "超过 200GW",
            "2026 年定价口径",
            "第三名",
            "约二十个来源",
            "三倍以上差距",
            "成本下降 50％",
            "$1.2 亿",
        ],
    )
    def test_numeric_texts_detected(self, text: str) -> None:
        assert has_numeric_assertion(text)

    @pytest.mark.parametrize("text", ["多源印证的选型建议", "权威来源口径一致", "一方观点", "三方讨论"])
    def test_plain_texts_not_detected(self, text: str) -> None:
        assert not has_numeric_assertion(text)

    def test_find_violations_indices(self) -> None:
        drafts = [
            _draft(text="没有引用的数字：增长 30%", evidence_ids=[]),
            _draft(text="有引用的数字：增长 20%", evidence_ids=["ea"]),
            _draft(text="无引用也无数字的推论", evidence_ids=[]),
        ]
        assert find_numeric_violations(drafts, {"ea", "eb"}) == [0]


# ---------------------------------------------------------------------------
# AC-1 marker 分配
# ---------------------------------------------------------------------------


class TestMarkers:
    def test_only_cited_evidence_gets_number_in_pool_order(self) -> None:
        # eb 未被任何块引用：不占号；ec 引用早于 ea，但池顺序为 ea/eb/ec
        drafts = [
            _draft(text="结论甲", evidence_ids=["ec"]),
            _draft(btype="evidence", text="证据展开", evidence_ids=["ea", "ec"]),
        ]
        assembly = _assemble(drafts, material=_material("ea", "eb", "ec"))
        markers = {c["evidence_id"]: c["marker"] for b in assembly.blocks for c in b["citations"]}
        assert markers == {"ea": "[1]", "ec": "[2]"}
        assert "eb" not in markers

    def test_marker_shared_across_blocks_and_dedup_in_block(self) -> None:
        drafts = [
            _draft(text="结论甲", evidence_ids=["ea", "ea", "eb"]),
            _draft(text="结论乙", evidence_ids=["ea"]),
        ]
        assembly = _assemble(drafts)
        first = assembly.blocks[0]["citations"]
        assert [c["evidence_id"] for c in first] == ["ea", "eb"]
        assert first[0]["marker"] == "[1]"
        assert assembly.blocks[1]["citations"][0]["marker"] == "[1]"

    def test_marker_always_formatted_by_engine(self) -> None:
        drafts = [_draft(text="结论", evidence_ids=["ea"])]
        assembly = _assemble(drafts)
        assert assembly.blocks[0]["citations"][0]["marker"] == "[1]"
        assert all(c["position"] == 1 for c in assembly.citation_rows)


# ---------------------------------------------------------------------------
# AC-2 白名单与幻觉计数
# ---------------------------------------------------------------------------


class TestWhitelist:
    def test_hallucinated_ids_removed_and_counted(self) -> None:
        drafts = [
            _draft(text="结论甲", evidence_ids=["ea", "ghost1"]),
            _draft(text="结论乙", evidence_ids=["ghost2", "eb"]),
        ]
        assembly = _assemble(drafts)
        cited = {c["evidence_id"] for b in assembly.blocks for c in b["citations"]}
        assert cited == {"ea", "eb"}
        assert assembly.audit["hallucinated_refs"] == 2

    def test_snippet_never_taken_from_model(self) -> None:
        drafts = [_draft(text="结论", evidence_ids=["ea"], quotes={})]
        assembly = _assemble(
            drafts,
            content_map={"ea": EvidenceText(snippet="库内摘要", content=None)},
        )
        assert assembly.blocks[0]["citations"][0]["snippet"] == "库内摘要"


# ---------------------------------------------------------------------------
# AC-3 snippet quote 原文匹配
# ---------------------------------------------------------------------------


class TestQuoteSnippet:
    def test_direct_quote_hit_returns_window(self) -> None:
        content = "背景介绍。官方报告称 P99 延迟低于 80ms，测试环境为 32 节点。尾部说明。"
        drafts = [
            _draft(
                text="性能口径",
                evidence_ids=["ea"],
                quotes={"ea": "P99 延迟低于 80ms"},
            )
        ]
        assembly = _assemble(
            drafts,
            content_map={"ea": EvidenceText(snippet="摘要", content=content)},
        )
        snippet = assembly.blocks[0]["citations"][0]["snippet"]
        assert "P99 延迟低于 80ms" in snippet
        assert len(snippet) <= 120
        assert assembly.audit["quote_verified_refs"] == 1

    def test_whitespace_normalized_hit(self) -> None:
        content = "报告写明 P99\n延迟低于 80ms，且未经复核。"
        drafts = [_draft(text="性能口径", evidence_ids=["ea"], quotes={"ea": "P99 延迟低于80ms"})]
        assembly = _assemble(
            drafts,
            content_map={"ea": EvidenceText(snippet="摘要", content=content)},
        )
        assert "P99" in assembly.blocks[0]["citations"][0]["snippet"]
        assert assembly.audit["quote_verified_refs"] == 1

    def test_miss_falls_back_to_evidence_snippet(self) -> None:
        drafts = [_draft(text="结论", evidence_ids=["ea"], quotes={"ea": "正文里根本没有的句子"})]
        assembly = _assemble(
            drafts,
            content_map={"ea": EvidenceText(snippet="检索摘要原文", content="完全不同的正文")},
        )
        citation = assembly.blocks[0]["citations"][0]
        assert citation["snippet"] == "检索摘要原文"
        assert assembly.audit["quote_verified_refs"] == 0

    def test_no_content_falls_back(self) -> None:
        drafts = [_draft(text="结论", evidence_ids=["ea"], quotes={"ea": "某句"})]
        assembly = _assemble(drafts, content_map={"ea": EvidenceText(snippet="仅摘要", content=None)})
        assert assembly.blocks[0]["citations"][0]["snippet"] == "仅摘要"

    def test_content_map_missing_falls_back_to_state_snippet(self) -> None:
        drafts = [_draft(text="结论", evidence_ids=["ea"], quotes={"ea": "某句"})]
        assembly = _assemble(drafts, content_map={})
        assert assembly.blocks[0]["citations"][0]["snippet"] == "证据 ea 摘要"


# ---------------------------------------------------------------------------
# AC-4/AC-5 缺源降级与数字断言审计
# ---------------------------------------------------------------------------


class TestBindingAudit:
    def test_unbound_plain_conclusion_forced_inferred(self) -> None:
        drafts = [_draft(text="纯推论性判断", confidence="cross_verified", evidence_ids=[])]
        assembly = _assemble(drafts)
        block = assembly.blocks[0]
        assert block["type"] == "conclusion"
        assert block["confidence"] == "inferred"
        assert assembly.audit["forced_inferred_blocks"] == 1
        assert assembly.audit["dropped_numeric_blocks"] == 0

    def test_numeric_unbound_conclusion_dropped(self) -> None:
        # 该块模拟自修复后仍无引用的终态：引擎只负责剔除（自修复调用在节点层）
        drafts = [
            _draft(text="无来源数字：增长 30%", confidence="cross_verified", evidence_ids=[]),
            _draft(text="有来源结论", confidence="single_source", evidence_ids=["ea"]),
        ]
        assembly = _assemble(drafts)
        texts = [b["text"] for b in assembly.blocks if b["type"] == "conclusion"]
        assert "无来源数字：增长 30%" not in texts
        assert assembly.audit["dropped_numeric_blocks"] == 1

    def test_bound_conclusion_keeps_llm_confidence(self) -> None:
        drafts = [_draft(text="多源结论", confidence="cross_verified", evidence_ids=["ea", "eb"])]
        assembly = _assemble(drafts)
        assert assembly.blocks[0]["confidence"] == "cross_verified"

    def test_audit_identity_claim_blocks_equals_bound_plus_inferred(self) -> None:
        drafts = [
            _draft(text="有引用结论", evidence_ids=["ea"]),
            _draft(text="无引用推论", evidence_ids=[]),
            _draft(btype="limitation", text="方法局限", evidence_ids=[]),
        ]
        assembly = _assemble(drafts, material=_material("ea"))
        audit = assembly.audit
        assert audit["claim_blocks"] == audit["bound_blocks"] + audit["forced_inferred_blocks"]
        assert audit["numeric_claim_binding_rate"] == 1.0


# ---------------------------------------------------------------------------
# AC-6 dispute 注入
# ---------------------------------------------------------------------------


class TestDisputeInjection:
    def test_pending_conflict_injected_cross_verified(self) -> None:
        assembly = _assemble(
            [_draft(text="结论", evidence_ids=["ea"])],
            conflicts=[_conflict("c1")],
            claims=[_claim("x", "口径 A 文本", "ea"), _claim("y", "口径 B 文本", "eb")],
        )
        disputes_blocks = _blocks_by_type(assembly, "dispute")
        assert len(disputes_blocks) == 1
        block = disputes_blocks[0]
        assert block["conflict_id"] == "c1"
        assert block["confidence"] == "cross_verified"
        assert [c["evidence_id"] for c in block["citations"]] == ["ea", "eb"]
        assert "对立议题" in block["text"] and "尚待人工裁决" in block["text"]

    def test_both_and_reject_verdicts_injected(self) -> None:
        assembly = _assemble(
            [_draft(text="结论", evidence_ids=["ea"])],
            conflicts=[_conflict("c1"), _conflict("c2", claim_text="另一起")],
            verdicts=[_verdict("c1", "both", note="注意口径"), _verdict("c2", "reject")],
        )
        disputes_blocks = _blocks_by_type(assembly, "dispute")
        assert {b["conflict_id"] for b in disputes_blocks} == {"c1", "c2"}
        assert all("裁决意见" in b["text"] for b in disputes_blocks)

    def test_resolved_and_single_side_not_injected(self) -> None:
        assembly = _assemble(
            [_draft(text="结论", evidence_ids=["ea"])],
            conflicts=[
                _conflict("c1", status="resolved", severity="medium"),
                _conflict("c2"),
            ],
            verdicts=[_verdict("c2", "evidence_a")],
        )
        assert _blocks_by_type(assembly, "dispute") == []

    def test_one_side_excluded_single_source_with_note(self) -> None:
        # eb 被用户剔除：材料池只有 ea
        assembly = _assemble(
            [_draft(text="结论", evidence_ids=["ea"])],
            material=_material("ea"),
            conflicts=[_conflict("c1", a="ea", b="eb")],
            claims=[_claim("x", "口径 A 文本", "ea")],
        )
        block = _blocks_by_type(assembly, "dispute")[0]
        assert block["confidence"] == "single_source"
        assert [c["evidence_id"] for c in block["citations"]] == ["ea"]
        assert "已由用户剔除" in block["text"]

    def test_both_sides_excluded_skipped_and_counted(self) -> None:
        assembly = _assemble(
            [_draft(text="结论", evidence_ids=["ea"])],
            material=_material("ea"),
            conflicts=[_conflict("c1", a="ex", b="ey")],
        )
        assert _blocks_by_type(assembly, "dispute") == []
        assert assembly.audit["skipped_disputes"] == 1

    def test_dispute_inserted_before_limitation(self) -> None:
        drafts = [
            _draft(text="结论", evidence_ids=["ea"]),
            _draft(btype="limitation", text="局限说明", evidence_ids=[]),
        ]
        assembly = _assemble(drafts, conflicts=[_conflict("c1")])
        types = [b["type"] for b in assembly.blocks]
        assert types.index("dispute") < types.index("limitation")


# ---------------------------------------------------------------------------
# AC-7 id / outline 形态
# ---------------------------------------------------------------------------


class TestIdsAndOutline:
    def test_block_and_claim_sequential_ids(self) -> None:
        drafts = [
            _draft(text="结论一", evidence_ids=["ea"]),
            _draft(btype="evidence", text="证据展开", evidence_ids=["eb"]),
        ]
        assembly = _assemble(drafts, conflicts=[_conflict("c1")])
        ids = [b["id"] for b in assembly.blocks]
        assert ids[0] == "block-01"
        assert {"block-01", "block-02"} <= set(ids)
        conclusion = assembly.blocks[0]
        evidence_block = next(b for b in assembly.blocks if b["type"] == "evidence")
        dispute_block = _blocks_by_type(assembly, "dispute")[0]
        assert conclusion["claim_id"] == "claim-01"
        assert dispute_block["claim_id"] == "claim-02"
        assert "claim_id" not in evidence_block

    def test_evidence_and_limitation_have_no_confidence(self) -> None:
        drafts = [
            _draft(btype="evidence", text="证据", evidence_ids=["ea"]),
            _draft(btype="limitation", text="局限", evidence_ids=[]),
        ]
        assembly = _assemble(drafts)
        for block in assembly.blocks:
            if block["type"] in {"evidence", "limitation"}:
                assert "confidence" not in block

    def test_outline_semantic_ids_conditional_sections(self) -> None:
        assembly = _assemble([_draft(text="结论", evidence_ids=["ea"])], conflicts=[_conflict("c1")])
        assert [s["id"] for s in assembly.outline] == [
            "sec-overview",
            "sec-findings",
            "sec-disputes",
            "sec-limitations",
        ]

    def test_outline_without_disputes(self) -> None:
        assembly = _assemble([_draft(text="结论", evidence_ids=["ea"])])
        assert [s["id"] for s in assembly.outline] == [
            "sec-overview",
            "sec-findings",
            "sec-limitations",
        ]

    def test_fallback_limitation_appended(self) -> None:
        assembly = _assemble([_draft(text="结论", evidence_ids=["ea"])])
        limitations = _blocks_by_type(assembly, "limitation")
        assert len(limitations) == 1
        assert "回溯" in limitations[0]["text"]

    def test_overview_fallback_when_no_conclusion(self) -> None:
        assembly = _assemble(
            [_draft(btype="evidence", text="只有证据展开", evidence_ids=["ea"])],
            question="向量数据库选型",
        )
        first = assembly.blocks[0]
        assert first["type"] == "conclusion"
        assert "向量数据库选型" in first["text"]
        assert first["confidence"] == "inferred"


# ---------------------------------------------------------------------------
# AC-8 机械映射
# ---------------------------------------------------------------------------


class TestMechanicalDrafts:
    def test_claims_to_conclusion_drafts(self) -> None:
        claims = [
            _claim("c1", "结论甲", "ea", "eb", confidence="cross_verified"),
            _claim("c2", "结论乙", "eb"),
        ]
        drafts = mechanical_drafts(claims, {"ea", "eb"})
        assert [d.type for d in drafts] == ["conclusion", "conclusion"]
        assert drafts[0].evidence_ids == ["ea", "eb"]
        assert drafts[0].confidence == "cross_verified"

    def test_unknown_and_excluded_evidence_filtered(self) -> None:
        claims = [_claim("c1", "结论", "ea", "ghost")]
        drafts = mechanical_drafts(claims, {"eb"})
        assert drafts[0].evidence_ids == []

    def test_degraded_assembly_still_complete_and_bound(self) -> None:
        # 无 LLM 路径：claim 机械映射 + dispute 注入，无 claim 时概述兜底
        claims = [_claim("x", "口径 A", "ea")]
        assembly = _assemble(
            mechanical_drafts(claims, {"ea", "eb"}),
            claims=claims,
            conflicts=[_conflict("c1")],
        )
        assert _blocks_by_type(assembly, "conclusion")
        assert _blocks_by_type(assembly, "dispute")
        assert _blocks_by_type(assembly, "limitation")
        for block in assembly.blocks:
            if block["type"] in {"conclusion", "dispute"} and block["confidence"] != "inferred":
                assert block["citations"]


# ---------------------------------------------------------------------------
# 引文关系行与 LLM 计划转换
# ---------------------------------------------------------------------------


class TestCitationRowsAndPlan:
    def test_citation_rows_shape_and_dedup(self) -> None:
        drafts = [
            _draft(text="结论甲", evidence_ids=["ea", "eb"]),
            _draft(btype="evidence", text="证据", evidence_ids=["ea"]),
        ]
        assembly = _assemble(drafts)
        rows = assembly.citation_rows
        assert len(rows) == 3
        first_block = assembly.blocks[0]["id"]
        row0 = next(r for r in rows if r["block_id"] == first_block and r["evidence_id"] == "ea")
        assert row0["position"] == 1
        assert row0["claim_id"] == "claim-01"
        assert row0["snippet"]
        evidence_block = next(b for b in assembly.blocks if b["type"] == "evidence")
        row_ev = next(r for r in rows if r["block_id"] == evidence_block["id"])
        assert row_ev["claim_id"] is None

    def test_drafts_from_plan_strips_blank_and_converts(self) -> None:
        plan = LLMReportPlan(
            blocks=[
                LLMBlockDraftModel(
                    type="conclusion",
                    text="有效结论",
                    confidence="single_source",
                    evidence_ids=["ea"],
                    quotes={"ea": "原句"},
                ),
                LLMBlockDraftModel(
                    type="limitation",
                    text="   ",
                    confidence="inferred",
                ),
            ]
        )
        drafts = drafts_from_plan(plan)
        assert len(drafts) == 1
        assert drafts[0].quotes == {"ea": "原句"}
