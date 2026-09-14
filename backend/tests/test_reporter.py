"""WP-5.7 / M2-2 Task 6 reporter 节点单元测试。

覆盖：
- ``default_outline``：返回 4 段；不同 template_id 都返回相同骨架（M1 简化）
- ``render_section`` 各类型：background / findings / conflicts / conclusion / 未知类型
- M2-2 冲突段：
  - 无冲突 / 待人工裁决（议题+双方口径+来源链接）
  - low/medium 自动收敛与单边人工裁决只入摘要、不渲染为待办
  - both/reject 裁决进入「分歧与局限」区块，含议题、双方来源、裁决理由、局限备注
- 结论段不夹带被舍弃 claim 文本（TR-6.1 / AC-16）
- 节点 ``run``：空 claims / 完整链路 / 已有 outline 沿用 / deps=None / 缺 template_id
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes.reporter import (
    default_outline,
    render_section,
    run,
)
from app.orchestrator.state import ConflictDict, EvidenceDict, ReportClaim, VerdictDict
from app.provider.client import StructuredCompletion
from app.reporting.schemas import LLMBlockDraftModel, LLMReportPlan

# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def _evidence(
    *,
    ev_id: str = "ea",
    title: str = "来源 A",
    url: str = "https://example.com/a",
    snippet: str = "口径 A 文本",
) -> EvidenceDict:
    return {
        "id": ev_id,
        "sub_question_id": "sq1",
        "url": url,
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


def _claim(
    *,
    cid: str = "01HZZ",
    text: str = "核心发现",
    confidence: str = "single_source",
    citations: list[dict[str, Any]] | None = None,
    divergence_flags: list[str] | None = None,
) -> ReportClaim:
    claim: ReportClaim = {
        "id": cid,
        "text": text,
        "confidence": confidence,  # type: ignore[typeddict-item]
        "citations": citations
        or [
            {
                "evidence_id": "e1",
                "url": "https://example.com/a",
                "title": "来源 A",
                "snippet": "片段 A",
            }
        ],
    }
    if divergence_flags is not None:
        claim["divergence_flags"] = divergence_flags
    return claim


def _conflict(
    *,
    cid: str = "c1",
    severity: str = "medium",
    ctype: str = "factual",
    claim: str = "议题描述",
    evidence_a_id: str = "ea",
    evidence_b_id: str = "eb",
    status: str = "awaiting_human",
) -> ConflictDict:
    return {
        "id": cid,
        "claim": claim,
        "evidence_a_id": evidence_a_id,
        "evidence_b_id": evidence_b_id,
        "type": ctype,
        "severity": severity,
        "status": status,  # type: ignore[typeddict-item]
    }


def _verdict(
    cid: str,
    choice: str,
    *,
    reason: str | None = "裁决理由",
    note: str | None = None,
) -> VerdictDict:
    verdict: VerdictDict = {
        "conflict_id": cid,
        "user_id": "u1",
        "choice": choice,
        "reason": reason,
    }
    if note is not None:
        verdict["additional_note"] = note
    return verdict


def _state(
    *,
    question: str | None = "研究问题",
    template_id: str | None = None,
    clarification: dict[str, Any] | None = None,
    claims: list[ReportClaim] | None = None,
    conflicts: list[ConflictDict] | None = None,
    verdicts: list[VerdictDict] | None = None,
    evidence: list[EvidenceDict] | None = None,
    outline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    s: dict[str, Any] = {
        "run_id": "run_test",
        "question": question,
    }
    if template_id is not None:
        s["template_id"] = template_id
    if clarification is not None:
        s["clarification"] = clarification
    if claims is not None:
        s["report_claims"] = claims
    if conflicts is not None:
        s["conflicts"] = conflicts
    if verdicts is not None:
        s["verdicts"] = verdicts
    if evidence is not None:
        s["standardized_evidence"] = evidence
    if outline is not None:
        s["report_outline"] = outline
    return s


_CONFLICT_SECTION = {"id": "conflicts", "title": "冲突与不确定性", "type": "conflicts"}
_CONCLUSION_SECTION = {"id": "conclusion", "title": "结论", "type": "conclusion"}


def _section(report: str, title: str) -> str:
    """从整篇报告中切出指定二级标题段落（到下一个 ## 之前）。"""
    marker = f"## {title}"
    start = report.index(marker)
    rest = report[start:]
    next_h = rest.find("\n## ", 2)
    return rest if next_h == -1 else rest[:next_h]


# ---------------------------------------------------------------------------
# default_outline
# ---------------------------------------------------------------------------


class TestDefaultOutline:
    def test_returns_four_sections(self) -> None:
        outline = default_outline("any-template")
        assert len(outline) == 4
        assert [s["id"] for s in outline] == ["background", "findings", "conflicts", "conclusion"]

    def test_returns_independent_copies(self) -> None:
        # 每次调用应返回新字典列表，避免外部修改污染默认模板
        a = default_outline("t")
        b = default_outline("t")
        assert a is not b
        a[0]["title"] = "改写"
        assert b[0]["title"] != "改写"

    def test_unknown_template_still_returns_generic(self) -> None:
        outline = default_outline("unknown_template_id")
        assert len(outline) == 4
        assert outline[0]["type"] == "background"


# ---------------------------------------------------------------------------
# render_section：background
# ---------------------------------------------------------------------------


class TestRenderBackground:
    def test_includes_question(self) -> None:
        section = {"id": "background", "title": "研究背景", "type": "background"}
        out = render_section(section, _state(question="光伏装机增长"))
        assert "## 研究背景" in out
        assert "**光伏装机增长**" in out

    def test_includes_clarification_goal_and_scope(self) -> None:
        section = {"id": "background", "title": "研究背景", "type": "background"}
        state = _state(
            question="x",
            clarification={"goal": "梳理趋势", "scope": "近 5 年"},
        )
        out = render_section(section, state)
        assert "研究目标：梳理趋势" in out
        assert "研究范围：近 5 年" in out

    def test_clarification_missing_keys(self) -> None:
        section = {"id": "background", "title": "研究背景", "type": "background"}
        # clarification 为 None → 不崩
        out = render_section(section, _state(question="x", clarification=None))
        assert "## 研究背景" in out
        assert "研究目标" not in out

    def test_trailing_period_not_doubled(self) -> None:
        """用户问题自带句末标点时，报告不再追加句号且无双句号。"""
        section = {"id": "background", "title": "研究背景", "type": "background"}
        out = render_section(section, _state(question="什么是 RAG？"))
        assert "。。" not in out
        assert "**什么是 RAG？**\n" in out
        # 无句末标点时统一补一个中文句号
        out_plain = render_section(section, _state(question="什么是 RAG"))
        assert "**什么是 RAG**。" in out_plain

    def test_dict_scope_rendered_as_chinese_text(self) -> None:
        """LLM 把 scope 返回成 include/exclude 字典时，不得向报告泄漏 Python 字典原文。"""
        section = {"id": "background", "title": "研究背景", "type": "background"}
        state = _state(
            question="对比两类数据库",
            clarification={
                "goal": "数据库选型对比",
                "scope": {
                    "include": ["JSON 索引机制", "查询性能"],
                    "exclude": ["非 JSON 场景"],
                },
            },
        )
        out = render_section(section, state)
        assert "研究范围：包含JSON 索引机制、查询性能；不包含非 JSON 场景" in out
        assert "{" not in out and "}" not in out and "'" not in out

    def test_list_and_scalar_scope_normalized(self) -> None:
        section = {"id": "background", "title": "研究背景", "type": "background"}
        out_list = render_section(
            section,
            _state(question="q", clarification={"scope": ["中国", "近一年"]}),
        )
        assert "研究范围：中国、近一年" in out_list


# ---------------------------------------------------------------------------
# render_section：findings
# ---------------------------------------------------------------------------


class TestRenderFindings:
    def test_empty_claims(self) -> None:
        section = {"id": "findings", "title": "核心发现", "type": "findings"}
        out = render_section(section, _state(claims=[]))
        assert "（暂无发现）" in out

    def test_lists_claims_with_confidence_and_citations(self) -> None:
        section = {"id": "findings", "title": "核心发现", "type": "findings"}
        claims = [
            _claim(
                cid="c1",
                text="增长 30%",
                citations=[{"evidence_id": "e1", "url": "https://x.com/a", "title": "A", "snippet": ""}],
            ),
            _claim(cid="c2", text="突破 200GW", confidence="cross_verified"),
        ]
        out = render_section(section, _state(claims=claims))
        assert "1. 增长 30%" in out
        assert "（置信度：单一来源）" in out
        assert "2. 突破 200GW" in out
        assert "（置信度：多源印证）" in out
        assert "confidence:" not in out
        assert "[1] [A](https://x.com/a)" in out
        assert "**引用：**" in out

    def test_multiple_citations_per_claim(self) -> None:
        section = {"id": "findings", "title": "核心发现", "type": "findings"}
        claims = [
            _claim(
                cid="c1",
                citations=[
                    {"evidence_id": "e1", "url": "https://x.com/a", "title": "A", "snippet": ""},
                    {"evidence_id": "e2", "url": "https://x.com/b", "title": "B", "snippet": ""},
                ],
            ),
        ]
        out = render_section(section, _state(claims=claims))
        assert "[1] [A](https://x.com/a)" in out
        assert "[1] [B](https://x.com/b)" in out

    def test_divergence_flag_claim_has_hint(self) -> None:
        """both 裁决保留的 claim 在发现段显式提示分歧存在。"""
        section = {"id": "findings", "title": "核心发现", "type": "findings"}
        claims = [_claim(cid="c1", text="210GW 增长 45%", divergence_flags=["cf1"])]
        out = render_section(section, _state(claims=claims))
        assert "存在观点分歧，详见「冲突与不确定性」段" in out


# ---------------------------------------------------------------------------
# render_section：conflicts（M2-2 Task 6）
# ---------------------------------------------------------------------------


class TestRenderConflicts:
    def test_no_conflicts(self) -> None:
        out = render_section(_CONFLICT_SECTION, _state())
        assert "未检测到重大冲突" in out

    def test_pending_conflict_renders_claim_and_both_sources(self) -> None:
        """待裁决 high 冲突：议题 + 双方口径 + 双方来源链接。"""
        evidence = [
            _evidence(ev_id="ea", title="统计局报告", url="https://stats.gov.cn/x", snippet="新增 210GW"),
            _evidence(ev_id="eb", title="协会报告", url="https://assoc.org.cn/x", snippet="并网仅 120GW"),
        ]
        conflict = _conflict(
            cid="c1",
            severity="high",
            ctype="methodological",
            claim="2026 光伏新增装机口径",
        )
        out = render_section(_CONFLICT_SECTION, _state(conflicts=[conflict], evidence=evidence))
        assert "### 待人工裁决" in out
        assert "议题：2026 光伏新增装机口径" in out
        assert "口径/方法冲突，严重度：高" in out
        assert "新增 210GW" in out and "并网仅 120GW" in out
        assert "[统计局报告](https://stats.gov.cn/x)" in out
        assert "[协会报告](https://assoc.org.cn/x)" in out

    def test_auto_resolved_conflict_not_rendered_as_todo(self) -> None:
        """TR-5.1：low/medium 自动收敛只入摘要，不渲染待办区块。"""
        conflict = _conflict(cid="c1", severity="medium", status="resolved")
        out = render_section(_CONFLICT_SECTION, _state(conflicts=[conflict]))
        assert "待人工裁决" not in out
        assert "分歧与局限" not in out
        assert "1 条由系统依据来源权威性自动收敛" in out
        assert "所有冲突均已收敛，未保留未消解分歧" in out

    def test_single_side_verdict_not_in_divergence(self) -> None:
        """evidence_a/b 单边裁决只入收敛摘要，不展开。"""
        conflict = _conflict(cid="c1", status="resolved")
        out = render_section(
            _CONFLICT_SECTION,
            _state(conflicts=[conflict], verdicts=[_verdict("c1", "evidence_a")]),
        )
        assert "分歧与局限" not in out
        assert "1 条经人工裁决采纳单一口径后收敛" in out

    def test_both_verdict_renders_divergence_block(self) -> None:
        """TR-6.1：both 裁决进「分歧与局限」，含议题/双方来源/裁决/理由/备注。"""
        evidence = [
            _evidence(ev_id="ea", title="统计局报告", url="https://stats.gov.cn/x", snippet="新增 210GW"),
            _evidence(ev_id="eb", title="协会报告", url="https://assoc.org.cn/x", snippet="并网仅 120GW"),
        ]
        conflict = _conflict(cid="c1", severity="high", ctype="methodological", claim="装机口径分歧")
        verdict = _verdict(
            "c1",
            "both",
            reason="双方统计口径不同，各自成立",
            note="引用时必须注明口径差异",
        )
        out = render_section(
            _CONFLICT_SECTION,
            _state(conflicts=[conflict], verdicts=[verdict], evidence=evidence),
        )
        assert "### 分歧与局限" in out
        assert "议题：装机口径分歧" in out
        assert "[统计局报告](https://stats.gov.cn/x)" in out
        assert "[协会报告](https://assoc.org.cn/x)" in out
        assert "裁决意见：双方观点并存" in out
        assert "裁决理由：双方统计口径不同，各自成立" in out
        assert "局限备注：引用时必须注明口径差异" in out
        # 已裁决冲突不再挂在「待人工裁决」
        assert "### 待人工裁决" not in out

    def test_reject_verdict_falls_back_to_evidence_text(self) -> None:
        """TR-6.1：reject 后双方 claim 已移除，口径文本回退证据 snippet，来源仍完整。"""
        evidence = [
            _evidence(ev_id="ea", title="来源甲", url="https://a.example/x", snippet="甲方说法"),
            _evidence(ev_id="eb", title="来源乙", url="https://b.example/x", snippet="乙方说法"),
        ]
        conflict = _conflict(cid="c1", claim="互斥说法")
        verdict = _verdict("c1", "reject", reason="双方均证据不足")
        out = render_section(
            _CONFLICT_SECTION,
            # claims 为空模拟 critic 双弃后 report_claims 不含双方
            _state(conflicts=[conflict], verdicts=[verdict], evidence=evidence, claims=[]),
        )
        assert "甲方说法" in out and "乙方说法" in out
        assert "[来源甲](https://a.example/x)" in out
        assert "[来源乙](https://b.example/x)" in out
        assert "裁决意见：双方口径均不采纳" in out

    def test_mixed_pending_auto_and_divergence(self) -> None:
        c_auto = _conflict(cid="c1", severity="medium", status="resolved")
        c_pending = _conflict(cid="c2", severity="high", claim="待裁决议题")
        c_both = _conflict(cid="c3", severity="high", claim="并存议题")
        out = render_section(
            _CONFLICT_SECTION,
            _state(
                conflicts=[c_auto, c_pending, c_both],
                verdicts=[_verdict("c3", "both", reason="两说并存")],
            ),
        )
        assert "待裁决议题" in out
        assert "并存议题" in out
        assert "1 条由系统依据来源权威性自动收敛" in out


# ---------------------------------------------------------------------------
# render_section：conclusion（M2-2 Task 6）
# ---------------------------------------------------------------------------


class TestRenderConclusion:
    def test_no_conflicts_branch(self) -> None:
        out = render_section(_CONCLUSION_SECTION, _state(claims=[_claim()], conflicts=[]))
        assert "## 结论" in out
        assert "1 条核心发现" in out
        assert "待处理冲突" not in out
        assert "不确定性保留" not in out

    def test_pending_and_divergence_counts(self) -> None:
        conflicts = [_conflict(cid="c1"), _conflict(cid="c2")]
        verdicts = [_verdict("c2", "both")]
        out = render_section(
            _CONCLUSION_SECTION,
            _state(claims=[_claim()], conflicts=conflicts, verdicts=verdicts),
        )
        assert "1 条待处理冲突尚待人工裁决" in out
        assert "1 条分歧经裁决后作为不确定性保留" in out

    def test_discarded_claim_text_not_in_conclusion(self) -> None:
        """TR-6.1/AC-16：结论段绝不夹带被舍弃 claim 的文本。"""
        discarded_a = "被舍弃的甲方独家结论 210GW"
        discarded_b = "被舍弃的乙方独家结论 120GW"
        claims = [
            _claim(cid="keep", text="保留的中立结论"),
        ]
        conflicts = [_conflict(cid="c1")]
        verdicts = [_verdict("c1", "reject")]
        state = _state(claims=claims, conflicts=conflicts, verdicts=verdicts)
        out = render_section(_CONCLUSION_SECTION, state)
        assert discarded_a not in out
        assert discarded_b not in out
        assert "保留的中立结论" not in out  # 结论段只计数，不回写 claim 文本


# ---------------------------------------------------------------------------
# render_section：未知 type 兜底
# ---------------------------------------------------------------------------


class TestRenderUnknownType:
    def test_unknown_type_renders_placeholder(self) -> None:
        section = {"id": "mystery", "title": "未知段落", "type": "unknown"}
        out = render_section(section, _state())
        assert "## 未知段落" in out
        assert "暂未实现" in out


# ---------------------------------------------------------------------------
# 节点 run
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_no_claims_no_conflicts(self) -> None:
        patch = await run(_state())
        assert len(patch["report_outline"]) == 4
        assert "## 研究背景" in patch["report_draft"]
        assert "## 核心发现" in patch["report_draft"]
        assert "## 冲突与不确定性" in patch["report_draft"]
        assert "## 结论" in patch["report_draft"]

    @pytest.mark.asyncio
    async def test_full_pipeline_both_divergence_rendered(self) -> None:
        """TR-6.1 渲染快照：both 场景报告含双方来源链接与议题。"""
        evidence = [
            _evidence(ev_id="ea", title="统计局报告", url="https://stats.gov.cn/x", snippet="210GW 口径"),
            _evidence(ev_id="eb", title="协会报告", url="https://assoc.org.cn/x", snippet="120GW 口径"),
        ]
        claims = [
            _claim(
                cid="cl-a",
                text="210GW 口径",
                citations=[
                    {
                        "evidence_id": "ea",
                        "url": "https://stats.gov.cn/x",
                        "title": "统计局报告",
                        "snippet": "",
                    }
                ],
                divergence_flags=["c1"],
            ),
            _claim(
                cid="cl-b",
                text="120GW 口径",
                citations=[
                    {"evidence_id": "eb", "url": "https://assoc.org.cn/x", "title": "协会报告", "snippet": ""}
                ],
                divergence_flags=["c1"],
            ),
        ]
        conflicts = [_conflict(cid="c1", severity="high", claim="装机规模口径分歧")]
        verdicts = [_verdict("c1", "both", reason="口径并存", note="注明统计口径")]
        patch = await run(
            _state(
                claims=claims,
                conflicts=conflicts,
                verdicts=verdicts,
                evidence=evidence,
            )
        )
        md = patch["report_draft"]
        assert "装机规模口径分歧" in md
        assert "[统计局报告](https://stats.gov.cn/x)" in md
        assert "[协会报告](https://assoc.org.cn/x)" in md
        assert "双方观点并存" in md
        assert "口径并存" in md and "注明统计口径" in md
        conclusion = _section(md, "结论")
        assert "210GW 口径" not in conclusion and "120GW 口径" not in conclusion

    @pytest.mark.asyncio
    async def test_reject_discarded_claim_absent_everywhere(self) -> None:
        """TR-6.1：reject 双弃 claim 文本不出现在发现段与结论段，仅冲突段留痕。"""
        discarded = "被舍弃的排他性结论"
        evidence = [
            _evidence(ev_id="ea", title="来源甲", url="https://a.example/x", snippet=discarded),
            _evidence(ev_id="eb", title="来源乙", url="https://b.example/x", snippet="对立结论"),
        ]
        conflicts = [_conflict(cid="c1", claim="互斥结论")]
        verdicts = [_verdict("c1", "reject")]
        patch = await run(_state(claims=[], conflicts=conflicts, verdicts=verdicts, evidence=evidence))
        md = patch["report_draft"]
        assert _section(md, "核心发现") != ""
        assert discarded not in _section(md, "核心发现")
        assert discarded not in _section(md, "结论")
        # 冲突段仍保留证据文本以便用户追溯
        assert discarded in _section(md, "冲突与不确定性")

    @pytest.mark.asyncio
    async def test_existing_outline_reused(self) -> None:
        custom_outline = [
            {"id": "background", "title": "研究背景", "type": "background"},
            {"id": "findings", "title": "核心发现", "type": "findings"},
        ]
        patch = await run(_state(outline=custom_outline))
        # 未提供 conflicts / conclusion 类型时，自定义 outline 不被默认补充
        assert len(patch["report_outline"]) == 2

    @pytest.mark.asyncio
    async def test_no_deps_works(self) -> None:
        patch = await run(_state(), deps=None)
        assert "## 研究背景" in patch["report_draft"]

    @pytest.mark.asyncio
    async def test_template_id_drives_outline(self) -> None:
        # M1 任意 template_id 都返回同一骨架
        patch = await run(_state(template_id="custom_tpl"))
        assert [s["id"] for s in patch["report_outline"]] == [
            "background",
            "findings",
            "conflicts",
            "conclusion",
        ]


# ---------------------------------------------------------------------------
# M2-7：结构化终稿节点接线（LLM 计划 / 自修复 / 机械降级）
# ---------------------------------------------------------------------------


def _plan_completion(blocks: list[LLMBlockDraftModel], *, tokens: int = 77) -> StructuredCompletion:
    return StructuredCompletion(parsed=LLMReportPlan(blocks=blocks), usage={"total_tokens": tokens})


def _fake_llm(*, side_effect: list[Any] | None = None, completion: Any = None) -> MagicMock:
    llm = MagicMock(name="fake_report_llm")
    if side_effect is not None:
        llm.complete_structured = AsyncMock(side_effect=side_effect)
    else:
        llm.complete_structured = AsyncMock(return_value=completion)
    return llm


def _deps(llm: MagicMock | None) -> NodeDeps:
    return NodeDeps(run_id="run_test", team_id="t1", trace_id="tr1", llm=llm)


class TestStructuredReport:
    @pytest.mark.asyncio
    async def test_llm_plan_bound_into_blocks_with_markers(self) -> None:
        evidence = [_evidence(ev_id="ea"), _evidence(ev_id="eb")]
        plan = _plan_completion(
            [
                LLMBlockDraftModel(
                    type="conclusion",
                    text="已形成两条清晰技术路线",
                    confidence="cross_verified",
                    evidence_ids=["ea", "eb"],
                ),
                LLMBlockDraftModel(type="limitation", text="样本范围有限", confidence="inferred"),
            ]
        )
        patch = await run(_state(evidence=evidence), deps=_deps(_fake_llm(completion=plan)))
        blocks = patch["report_blocks"]
        conclusion = next(b for b in blocks if b["type"] == "conclusion")
        assert [c["marker"] for c in conclusion["citations"]] == ["[1]", "[2]"]
        assert all(c["snippet"] for c in conclusion["citations"])
        assert patch["reporter_degraded"] is False
        assert patch["token_used"] == 77
        assert any(b["type"] == "limitation" for b in blocks)

    @pytest.mark.asyncio
    async def test_hallucinated_evidence_id_removed(self) -> None:
        evidence = [_evidence(ev_id="ea")]
        plan = _plan_completion(
            [
                LLMBlockDraftModel(
                    type="conclusion",
                    text="综合结论",
                    confidence="cross_verified",
                    evidence_ids=["ea", "ghost-id"],
                )
            ]
        )
        deps = _deps(_fake_llm(completion=plan))
        patch = await run(_state(evidence=evidence), deps=deps)
        ids = {c["evidence_id"] for b in patch["report_blocks"] for c in b["citations"]}
        assert ids == {"ea"}
        assert deps.report_assembly is not None
        assert deps.report_assembly.audit["hallucinated_refs"] == 1

    @pytest.mark.asyncio
    async def test_numeric_violation_triggers_one_repair_call(self) -> None:
        evidence = [_evidence(ev_id="ea")]
        first = _plan_completion(
            [LLMBlockDraftModel(type="conclusion", text="同比增长 30%", confidence="cross_verified")],
            tokens=40,
        )
        repaired = _plan_completion(
            [
                LLMBlockDraftModel(
                    type="conclusion",
                    text="增长显著且有来源支撑",
                    confidence="single_source",
                    evidence_ids=["ea"],
                )
            ],
            tokens=35,
        )
        llm = _fake_llm(side_effect=[first, repaired])
        patch = await run(_state(evidence=evidence), deps=_deps(llm))
        assert llm.complete_structured.await_count == 2
        conclusion = next(b for b in patch["report_blocks"] if b["type"] == "conclusion")
        assert conclusion["text"] == "增长显著且有来源支撑"
        assert conclusion["citations"][0]["evidence_id"] == "ea"
        assert patch["token_used"] == 75

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_mechanical_mapping(self) -> None:
        evidence = [_evidence(ev_id="ea", snippet="机械路径摘要")]
        claims = [
            _claim(
                cid="cl1",
                text="来自 claim 的结论",
                citations=[
                    {
                        "evidence_id": "ea",
                        "url": "https://example.com/ea",
                        "title": "来源 ea",
                        "snippet": "机械路径摘要",
                    }
                ],
            )
        ]
        llm = _fake_llm(side_effect=[ProviderUnavailableError("熔断")])
        patch = await run(
            _state(evidence=evidence, claims=claims),
            deps=_deps(llm),
        )
        assert patch["reporter_degraded"] is True
        conclusion = next(b for b in patch["report_blocks"] if b["type"] == "conclusion")
        assert conclusion["text"] == "来自 claim 的结论"
        assert conclusion["citations"][0]["snippet"] == "机械路径摘要"

    @pytest.mark.asyncio
    async def test_no_llm_still_emits_structured_blocks(self) -> None:
        # deps 带 None llm（未配置/测试基线）：机械降级路径，结构不缺位
        evidence = [_evidence(ev_id="ea")]
        patch = await run(_state(evidence=evidence), deps=_deps(None))
        types = {b["type"] for b in patch["report_blocks"]}
        assert {"conclusion", "limitation"} <= types
        assert patch["reporter_degraded"] is True

    @pytest.mark.asyncio
    async def test_assembly_handed_to_deps_for_executor(self) -> None:
        evidence = [_evidence(ev_id="ea"), _evidence(ev_id="eb")]
        plan = _plan_completion(
            [
                LLMBlockDraftModel(
                    type="conclusion",
                    text="结论",
                    confidence="single_source",
                    evidence_ids=["ea", "eb"],
                )
            ]
        )
        deps = _deps(_fake_llm(completion=plan))
        await run(_state(evidence=evidence), deps=deps)
        assembly = deps.report_assembly
        assert assembly is not None
        assert [s["id"] for s in assembly.outline] == [
            "sec-overview",
            "sec-findings",
            "sec-limitations",
        ]
        assert len(assembly.citation_rows) == 2
