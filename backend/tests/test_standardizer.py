"""WP-5.5 standardizer 节点单元测试。

覆盖：
- classify_source_level（gov/edu / 媒体 / 默认）
- classify_credibility（基础等级 + score 阶梯 + 夹到 D）
- dedupe_by_fingerprint（同 fingerprint 保留首条）
- 节点 run：空 evidence / 跨子问题去重 + 排序 / 子问题 evidence_ids 收敛 /
  单子问题直入直出
- 节点签名接受 ``deps=None``（向后兼容 M0）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.orchestrator.nodes.standardizer import (
    classify_credibility,
    classify_source_level,
    dedupe_by_fingerprint,
    run,
)
from app.orchestrator.state import EvidenceDict, SubQuestionDict

# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def _ev(
    *,
    ev_id: str,
    sub_question_id: str = "sq1",
    url: str = "https://example.com/x",
    domain: str = "example.com",
    title: str = "标题",
    snippet: str = "片段",
    fingerprint: str | None = None,
    source_type: str = "search",
    source_level: str = "tertiary",
    credibility: str = "C",
    relevance_score: float = 0.5,
    published_at: str | None = None,
    fetched_at: str | None = None,
) -> EvidenceDict:
    """构造 EvidenceDict，``fingerprint`` 缺省时基于 url+ev_id 生成便于测试。"""
    return {
        "id": ev_id,
        "sub_question_id": sub_question_id,
        "url": url,
        "domain": domain,
        "title": title,
        "snippet": snippet,
        "source_type": source_type,
        "source_level": source_level,
        "credibility": credibility,
        "fingerprint": fingerprint or f"fp-{ev_id}",
        "published_at": published_at,
        "fetched_at": fetched_at or datetime.now(tz=UTC).isoformat(),
        # standardizer 不消费 relevance_score 字段，但类型上需要兼容读取
        "relevance_score": relevance_score,  # type: ignore[typeddict-unknown-key]
    }


def _subq(
    subq_id: str,
    *,
    status: str = "succeeded",
    evidence_ids: list[str] | None = None,
) -> SubQuestionDict:
    return {
        "id": subq_id,
        "question": "调研",
        "depends_on": [],
        "status": status,  # type: ignore[typeddict-item]
        "evidence_ids": evidence_ids or [],
    }


def _state(*, evidence: list[EvidenceDict], subqs: list[SubQuestionDict]) -> dict[str, Any]:
    return {
        "run_id": "run_test",
        "evidence": evidence,
        "sub_questions": subqs,
    }


# ---------------------------------------------------------------------------
# classify_source_level
# ---------------------------------------------------------------------------


class TestClassifySourceLevel:
    def test_empty_domain_is_tertiary(self) -> None:
        assert classify_source_level("") == "tertiary"
        assert classify_source_level(None) == "tertiary"

    def test_gov_cn_is_primary(self) -> None:
        assert classify_source_level("www.stats.gov.cn") == "primary"
        assert classify_source_level("miit.gov.cn") == "primary"

    def test_gov_subdomain_is_primary(self) -> None:
        assert classify_source_level("subdomain.gov") == "primary"

    def test_edu_cn_is_primary(self) -> None:
        assert classify_source_level("www.tsinghua.edu.cn") == "primary"

    def test_academic_is_primary(self) -> None:
        assert classify_source_level("research.example.ac") == "primary"

    def test_known_news_keyword_is_secondary(self) -> None:
        assert classify_source_level("news.example.com") == "secondary"
        assert classify_source_level("reuters.com") == "secondary"
        assert classify_source_level("bbc.co.uk") == "secondary"
        assert classify_source_level("www.ft.com") == "secondary"

    def test_other_is_tertiary(self) -> None:
        assert classify_source_level("example.com") == "tertiary"
        assert classify_source_level("blog.example.org") == "tertiary"

    def test_case_insensitive(self) -> None:
        assert classify_source_level("NEWS.Example.COM") == "secondary"
        assert classify_source_level("XHNEWS.COM") == "secondary"


# ---------------------------------------------------------------------------
# classify_credibility
# ---------------------------------------------------------------------------


class TestClassifyCredibility:
    def test_primary_high_score_keeps_a(self) -> None:
        ev = _ev(ev_id="e1", domain="gov.cn", source_level="primary", relevance_score=0.95)
        assert classify_credibility(ev) == "A"

    def test_primary_mid_score_drops_to_b(self) -> None:
        ev = _ev(ev_id="e1", domain="gov.cn", source_level="primary", relevance_score=0.6)
        assert classify_credibility(ev) == "B"

    def test_primary_low_score_drops_to_c(self) -> None:
        ev = _ev(ev_id="e1", domain="gov.cn", source_level="primary", relevance_score=0.3)
        assert classify_credibility(ev) == "C"

    def test_secondary_high_score_keeps_b(self) -> None:
        ev = _ev(ev_id="e1", domain="news.com", source_level="secondary", relevance_score=0.9)
        assert classify_credibility(ev) == "B"

    def test_secondary_mid_score_drops_to_c(self) -> None:
        ev = _ev(ev_id="e1", domain="news.com", source_level="secondary", relevance_score=0.6)
        assert classify_credibility(ev) == "C"

    def test_secondary_low_score_drops_to_d(self) -> None:
        ev = _ev(ev_id="e1", domain="news.com", source_level="secondary", relevance_score=0.2)
        assert classify_credibility(ev) == "D"

    def test_tertiary_high_score_keeps_c(self) -> None:
        ev = _ev(ev_id="e1", domain="example.com", source_level="tertiary", relevance_score=0.9)
        assert classify_credibility(ev) == "C"

    def test_tertiary_mid_score_drops_to_d(self) -> None:
        ev = _ev(ev_id="e1", domain="example.com", source_level="tertiary", relevance_score=0.6)
        assert classify_credibility(ev) == "D"

    def test_tertiary_low_score_clamps_to_d(self) -> None:
        ev = _ev(ev_id="e1", domain="example.com", source_level="tertiary", relevance_score=0.05)
        assert classify_credibility(ev) == "D"

    def test_missing_relevance_score_treated_as_zero(self) -> None:
        # score=0 → 2 步降级
        ev = _ev(ev_id="e1", source_level="primary", relevance_score=0.0)
        assert classify_credibility(ev) == "C"


# ---------------------------------------------------------------------------
# dedupe_by_fingerprint
# ---------------------------------------------------------------------------


class TestDedupeByFingerprint:
    def test_empty(self) -> None:
        assert dedupe_by_fingerprint([]) == []

    def test_unique_kept(self) -> None:
        evs = [
            _ev(ev_id="e1", fingerprint="fp-a"),
            _ev(ev_id="e2", fingerprint="fp-b"),
        ]
        result = dedupe_by_fingerprint(evs)
        assert [e["id"] for e in result] == ["e1", "e2"]

    def test_dup_fingerprint_first_wins(self) -> None:
        evs = [
            _ev(ev_id="e1", fingerprint="fp-x", title="first"),
            _ev(ev_id="e2", fingerprint="fp-x", title="second"),
            _ev(ev_id="e3", fingerprint="fp-x", title="third"),
        ]
        result = dedupe_by_fingerprint(evs)
        assert len(result) == 1
        assert result[0]["id"] == "e1"
        assert result[0]["title"] == "first"


# ---------------------------------------------------------------------------
# 节点 run：基础路径
# ---------------------------------------------------------------------------


class TestRunBasic:
    @pytest.mark.asyncio
    async def test_no_evidence_returns_empty(self) -> None:
        subqs = [_subq("sq1")]
        patch = await run(_state(evidence=[], subqs=subqs))
        assert patch["standardized_evidence"] == []
        # 子问题原样返回（空 evidence → subqs 不变）
        assert patch["sub_questions"] == subqs

    @pytest.mark.asyncio
    async def test_no_deps_works(self) -> None:
        """deps=None 仍可运行（M0 向后兼容）。"""
        evidence = [_ev(ev_id="e1", domain="example.com")]
        patch = await run(_state(evidence=evidence, subqs=[]), deps=None)
        assert len(patch["standardized_evidence"]) == 1

    @pytest.mark.asyncio
    async def test_single_evidence_passes_through(self) -> None:
        evidence = [_ev(ev_id="e1", domain="example.com", relevance_score=0.9)]
        subqs = [_subq("sq1", evidence_ids=["e1"])]
        patch = await run(_state(evidence=evidence, subqs=subqs))
        assert len(patch["standardized_evidence"]) == 1
        ev = patch["standardized_evidence"][0]
        assert ev["id"] == "e1"
        # example.com → tertiary；score=0.9 → C
        assert ev["source_level"] == "tertiary"
        assert ev["credibility"] == "C"
        # 子问题 evidence_ids 收敛后仍保留
        assert patch["sub_questions"][0]["evidence_ids"] == ["e1"]


# ---------------------------------------------------------------------------
# 节点 run：跨子问题去重 + 排序
# ---------------------------------------------------------------------------


class TestRunDedupAndSort:
    @pytest.mark.asyncio
    async def test_cross_sub_question_dedup(self) -> None:
        # 两个子问题抓到同一份证据（同一 fingerprint）→ 仅保留首条
        ev1 = _ev(ev_id="e1", sub_question_id="sq1", fingerprint="shared")
        ev2 = _ev(ev_id="e2", sub_question_id="sq2", fingerprint="shared")
        subqs = [
            _subq("sq1", evidence_ids=["e1"]),
            _subq("sq2", evidence_ids=["e2"]),
        ]
        patch = await run(_state(evidence=[ev1, ev2], subqs=subqs))
        assert len(patch["standardized_evidence"]) == 1
        assert patch["standardized_evidence"][0]["id"] == "e1"
        # sq2.evidence_ids 中的 e2 因去重消失 → 收敛为空
        assert patch["sub_questions"][1]["evidence_ids"] == []
        # sq1.evidence_ids 中的 e1 保留
        assert patch["sub_questions"][0]["evidence_ids"] == ["e1"]

    @pytest.mark.asyncio
    async def test_sort_by_credibility_then_relevance_score_desc(self) -> None:
        # A 应排在 B 之前，相同 credibility 内按 relevance_score 降序
        evs = [
            _ev(ev_id="e_tertiary_low", domain="example.com", relevance_score=0.6),
            _ev(ev_id="e_secondary_high", domain="reuters.com", relevance_score=0.9),
            _ev(ev_id="e_primary_mid", domain="www.stats.gov.cn", relevance_score=0.6),
        ]
        subqs = [_subq("sq1")]
        patch = await run(_state(evidence=evs, subqs=subqs))
        result_ids = [e["id"] for e in patch["standardized_evidence"]]
        # 期望：B(primary+0.6) → B(secondary+0.9) → D(tertiary+0.6)
        # 同 B 按 relevance_score 降序 → secondary(0.9) 在前
        assert result_ids == ["e_secondary_high", "e_primary_mid", "e_tertiary_low"]

    @pytest.mark.asyncio
    async def test_sort_within_same_credibility(self) -> None:
        # 两个 secondary 都是 B（同 source_level=secondary，score>=0.8）→ 按 relevance_score 降序
        evs = [
            _ev(ev_id="low", domain="news.com", source_level="secondary", relevance_score=0.85),
            _ev(ev_id="high", domain="news.com", source_level="secondary", relevance_score=0.95),
        ]
        patch = await run(_state(evidence=evs, subqs=[_subq("sq1")]))
        result_ids = [e["id"] for e in patch["standardized_evidence"]]
        assert result_ids == ["high", "low"]


# ---------------------------------------------------------------------------
# 节点 run：子问题 evidence_ids 收敛
# ---------------------------------------------------------------------------


class TestRunSubQuestionReindex:
    @pytest.mark.asyncio
    async def test_drops_orphan_evidence_ids(self) -> None:
        # 子问题引用了不存在的 evidence_id（不在 evidence 列表里）→ 仍按"未在 kept_ids 中"过滤
        ev = _ev(ev_id="e1", domain="example.com")
        subqs = [_subq("sq1", evidence_ids=["e1", "ghost-id"])]
        patch = await run(_state(evidence=[ev], subqs=subqs))
        assert patch["sub_questions"][0]["evidence_ids"] == ["e1"]

    @pytest.mark.asyncio
    async def test_keeps_all_when_no_dedup(self) -> None:
        ev1 = _ev(ev_id="e1", fingerprint="fp-1")
        ev2 = _ev(ev_id="e2", fingerprint="fp-2")
        subqs = [
            _subq("sq1", evidence_ids=["e1"]),
            _subq("sq2", evidence_ids=["e2"]),
        ]
        patch = await run(_state(evidence=[ev1, ev2], subqs=subqs))
        assert patch["sub_questions"][0]["evidence_ids"] == ["e1"]
        assert patch["sub_questions"][1]["evidence_ids"] == ["e2"]

    @pytest.mark.asyncio
    async def test_sub_questions_preserved_when_no_evidence(self) -> None:
        subqs = [_subq("sq1", evidence_ids=["whatever"])]
        patch = await run(_state(evidence=[], subqs=subqs))
        # 无 evidence 时 sub_questions 原样返回（state 端收敛由其它阶段负责）
        assert patch["sub_questions"] == subqs


# ---------------------------------------------------------------------------
# 节点 run：与 WP-5.4 联动契约（保证与 researcher_fan_out 输出的对接）
# ---------------------------------------------------------------------------


class TestRunIntegrationWithResearcherFanOut:
    @pytest.mark.asyncio
    async def test_recognizes_tertiary_placeholder_from_researcher(self) -> None:
        """researcher_fan_out 输出 source_level='tertiary' / credibility='C' 占位，
        standardizer 必须重新判定（这里 domain=www.stats.gov.cn → primary/A）。"""
        ev = _ev(
            ev_id="e1",
            domain="www.stats.gov.cn",
            source_level="tertiary",  # WP-5.4 占位
            credibility="C",  # WP-5.4 占位
            relevance_score=0.85,
        )
        patch = await run(_state(evidence=[ev], subqs=[]))
        assert patch["standardized_evidence"][0]["source_level"] == "primary"
        assert patch["standardized_evidence"][0]["credibility"] == "A"
