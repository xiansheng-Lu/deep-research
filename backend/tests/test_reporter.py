"""WP-5.7 reporter 节点单元测试。

覆盖：
- ``default_outline``：返回 4 段；不同 template_id 都返回相同骨架（M1 简化）
- ``render_section`` 各类型：background / findings / conflicts / conclusion / 未知类型
- 节点 ``run``：空 claims / 多 claims + 冲突 + verdicts / 已有 outline 沿用 /
  deps=None / 缺 template_id 也走默认
- Markdown 渲染关键字段：标题 / 引用链接 / 冲突严重度
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.nodes.reporter import (
    default_outline,
    render_section,
    run,
)
from app.orchestrator.state import ConflictDict, ReportClaim

# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def _claim(
    *,
    cid: str = "01HZZ",
    text: str = "核心发现",
    confidence: str = "single_source",
    citations: list[dict] | None = None,
) -> ReportClaim:
    return {
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


def _conflict(
    *,
    cid: str = "c1",
    severity: str = "medium",
    claim: str = "议题描述",
    evidence_a_id: str = "ea",
    evidence_b_id: str = "eb",
) -> ConflictDict:
    return {
        "id": cid,
        "claim": claim,
        "evidence_a_id": evidence_a_id,
        "evidence_b_id": evidence_b_id,
        "type": "factual",
        "severity": severity,  # type: ignore[typeddict-item]
        "status": "detected",
    }


def _state(
    *,
    question: str | None = "研究问题",
    template_id: str | None = None,
    clarification: dict | None = None,
    claims: list[ReportClaim] | None = None,
    conflicts: list[ConflictDict] | None = None,
    verdicts: list[dict] | None = None,
    outline: list[dict] | None = None,
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
    if outline is not None:
        s["report_outline"] = outline
    return s


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
        out = render_section(section, _state(question="光伏装机增长"), [], [])
        assert "## 研究背景" in out
        assert "**光伏装机增长**" in out

    def test_includes_clarification_goal_and_scope(self) -> None:
        section = {"id": "background", "title": "研究背景", "type": "background"}
        state = _state(
            question="x",
            clarification={"goal": "梳理趋势", "scope": "近 5 年"},
        )
        out = render_section(section, state, [], [])
        assert "研究目标：梳理趋势" in out
        assert "研究范围：近 5 年" in out

    def test_clarification_missing_keys(self) -> None:
        section = {"id": "background", "title": "研究背景", "type": "background"}
        # clarification 为 None → 不崩
        out = render_section(section, _state(question="x", clarification=None), [], [])
        assert "## 研究背景" in out
        assert "研究目标" not in out


# ---------------------------------------------------------------------------
# render_section：findings
# ---------------------------------------------------------------------------


class TestRenderFindings:
    def test_empty_claims(self) -> None:
        section = {"id": "findings", "title": "核心发现", "type": "findings"}
        out = render_section(section, _state(claims=[]), [], [])
        assert "（暂无发现）" in out

    def test_lists_claims_with_confidence_and_citations(self) -> None:
        section = {"id": "findings", "title": "核心发现", "type": "findings"}
        claims = [
            _claim(cid="c1", text="增长 30%", citations=[{"evidence_id": "e1", "url": "https://x.com/a", "title": "A", "snippet": ""}]),
            _claim(cid="c2", text="突破 200GW", confidence="cross_verified"),
        ]
        out = render_section(section, _state(claims=claims), [], [])
        assert "1. 增长 30%" in out
        assert "confidence: single_source" in out
        assert "2. 突破 200GW" in out
        assert "confidence: cross_verified" in out
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
        out = render_section(section, _state(claims=claims), [], [])
        assert "[1] [A](https://x.com/a)" in out
        assert "[1] [B](https://x.com/b)" in out


# ---------------------------------------------------------------------------
# render_section：conflicts
# ---------------------------------------------------------------------------


class TestRenderConflicts:
    def test_no_conflicts(self) -> None:
        section = {"id": "conflicts", "title": "冲突与不确定性", "type": "conflicts"}
        out = render_section(section, _state(), [], [])
        assert "未检测到重大冲突" in out

    def test_lists_pending_conflicts(self) -> None:
        section = {"id": "conflicts", "title": "冲突与不确定性", "type": "conflicts"}
        c = _conflict(cid="c1", severity="high", claim="数据口径分歧")
        out = render_section(section, _state(), [c], [])
        assert "severity=high" in out
        assert "数据口径分歧" in out
        assert "ea" in out and "eb" in out

    def test_resolved_conflicts_omit(self) -> None:
        section = {"id": "conflicts", "title": "冲突与不确定性", "type": "conflicts"}
        c = _conflict(cid="c1")
        verdicts = [{"conflict_id": "c1", "user_id": "u1", "choice": "evidence_a", "reason": None}]
        out = render_section(section, _state(), [c], verdicts)
        assert "所有冲突已由用户裁决" in out

    def test_mixed_pending_and_resolved(self) -> None:
        section = {"id": "conflicts", "title": "冲突与不确定性", "type": "conflicts"}
        c_resolved = _conflict(cid="c1", claim="已裁决")
        c_pending = _conflict(cid="c2", severity="high", claim="待处理")
        verdicts = [{"conflict_id": "c1", "user_id": "u1", "choice": "evidence_a", "reason": None}]
        out = render_section(section, _state(conflicts=[c_resolved, c_pending]), [c_resolved, c_pending], verdicts)
        assert "待处理" in out
        assert "已裁决" not in out


# ---------------------------------------------------------------------------
# render_section：conclusion
# ---------------------------------------------------------------------------


class TestRenderConclusion:
    def test_summarizes_counts(self) -> None:
        section = {"id": "conclusion", "title": "结论", "type": "conclusion"}
        claims = [_claim(cid="c1"), _claim(cid="c2")]
        conflicts = [_conflict(cid="c1")]
        out = render_section(section, _state(claims=claims, conflicts=conflicts), conflicts, [])
        assert "## 结论" in out
        assert "2 条核心发现" in out
        assert "1 条待处理冲突" in out

    def test_no_conflicts_branch(self) -> None:
        section = {"id": "conclusion", "title": "结论", "type": "conclusion"}
        out = render_section(section, _state(claims=[_claim()], conflicts=[]), [], [])
        assert "## 结论" in out
        assert "待处理冲突" not in out


# ---------------------------------------------------------------------------
# render_section：未知 type 兜底
# ---------------------------------------------------------------------------


class TestRenderUnknownType:
    def test_unknown_type_renders_placeholder(self) -> None:
        section = {"id": "mystery", "title": "未知段落", "type": "unknown"}
        out = render_section(section, _state(), [], [])
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
    async def test_full_pipeline_markdown_contains_all(self) -> None:
        claims = [
            _claim(cid="c1", text="发现 A"),
            _claim(cid="c2", text="发现 B"),
        ]
        conflicts = [_conflict(cid="c1", severity="high")]
        verdicts: list[dict] = []
        patch = await run(_state(claims=claims, conflicts=conflicts, verdicts=verdicts))
        md = patch["report_draft"]
        assert "**研究问题**" in md
        assert "1. 发现 A" in md
        assert "2. 发现 B" in md
        assert "severity=high" in md
        assert "## 结论" in md

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
