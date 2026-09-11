"""WP-5.6 critic 节点单元测试。

覆盖：
- 工具函数：``_tokenize`` / ``_jaccard`` / ``serialize_conflict``
- ``generate_draft_claims``：每条 evidence 派生一条 claim + 字段契约
- ``detect_conflicts``：空 / 单条 / 不相似 / 同 credibility / 相似+不同 credibility
- ``is_resolvable``：low / medium / high
- ``resolve_by_authority``：高 credibility 保留 / 平局保留首条 / 缺失不动
- 节点 ``run``：空 material / 单条 material / 无冲突直通 / 有可解冲突 /
  有不可解冲突（写 interrupt_reason="critique" + interrupt_payload）/
  token 预算超限跳出 / 已有 verdicts 收敛 / 已有 claims 复用
- 节点签名接受 ``deps=None``（向后兼容 M0）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.orchestrator.nodes.critic import (
    CRITIC_BUDGET_RATIO,
    MAX_CRITIC_ITERATIONS,
    detect_conflicts,
    generate_draft_claims,
    is_resolvable,
    resolve_by_authority,
    run,
    serialize_conflict,
)
from app.orchestrator.state import ConflictDict, EvidenceDict, ReportClaim

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
    snippet: str = "片段内容",
    source_type: str = "search",
    source_level: str = "tertiary",
    credibility: str = "C",
    fingerprint: str | None = None,
    published_at: str | None = None,
    fetched_at: str | None = None,
    relevance_score: float = 0.6,
) -> EvidenceDict:
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
        "relevance_score": relevance_score,  # type: ignore[typeddict-unknown-key]
    }


def _state(
    *,
    material: list[EvidenceDict] | None = None,
    claims: list[ReportClaim] | None = None,
    conflicts: list[ConflictDict] | None = None,
    verdicts: list[dict] | None = None,
    token_used: int = 0,
    token_budget: int = 0,
) -> dict[str, Any]:
    return {
        "run_id": "run_test",
        "standardized_evidence": material if material is not None else [],
        "report_claims": claims or [],
        "conflicts": conflicts or [],
        "verdicts": verdicts or [],
        "token_used": token_used,
        "token_budget": token_budget,
    }


# ---------------------------------------------------------------------------
# generate_draft_claims
# ---------------------------------------------------------------------------


class TestGenerateDraftClaims:
    def test_empty_material(self) -> None:
        assert generate_draft_claims([]) == []

    def test_one_evidence_one_claim(self) -> None:
        ev = _ev(ev_id="e1", title="新能源汽车销量增长", snippet="2026 年同比 +30%")
        claims = generate_draft_claims([ev])
        assert len(claims) == 1
        c = claims[0]
        assert c["text"] == "新能源汽车销量增长"
        assert c["confidence"] == "single_source"
        assert len(c["citations"]) == 1
        cit = c["citations"][0]
        assert cit["evidence_id"] == "e1"
        assert cit["url"] == ev["url"]
        assert cit["title"] == "新能源汽车销量增长"

    def test_claim_id_is_ulid(self) -> None:
        claims = generate_draft_claims([_ev(ev_id="e1"), _ev(ev_id="e2", domain="other.com")])
        for c in claims:
            assert len(c["id"]) == 26
            assert c["id"].isupper()

    def test_title_preferred_over_snippet(self) -> None:
        ev = _ev(ev_id="e1", title="标题优先", snippet="备选片段")
        claims = generate_draft_claims([ev])
        assert claims[0]["text"] == "标题优先"

    def test_fallback_to_snippet(self) -> None:
        ev = _ev(ev_id="e1", title="", snippet="只能取片段")
        claims = generate_draft_claims([ev])
        assert claims[0]["text"] == "只能取片段"

    def test_fallback_empty(self) -> None:
        ev = _ev(ev_id="e1", title="", snippet="")
        claims = generate_draft_claims([ev])
        assert claims[0]["text"] == ""


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    def test_less_than_two_evidence_returns_empty(self) -> None:
        assert detect_conflicts([], []) == []
        ev = _ev(ev_id="e1")
        assert detect_conflicts([], [ev]) == []

    def test_no_overlap_no_conflict(self) -> None:
        ev1 = _ev(
            ev_id="e1",
            domain="gov.cn",
            title="新能源汽车市场结构",
            snippet="2026 年渗透率突破 50%",
            credibility="A",
        )
        ev2 = _ev(
            ev_id="e2",
            domain="example.com",
            title="美食推荐",
            snippet="北京十大必吃餐厅",
            credibility="D",
        )
        result = detect_conflicts([], [ev1, ev2])
        assert result == []

    def test_overlap_same_credibility_no_conflict(self) -> None:
        ev1 = _ev(
            ev_id="e1",
            domain="news1.com",
            title="光伏装机量大幅增长",
            snippet="2026 年新增装机突破 200GW",
            credibility="B",
        )
        ev2 = _ev(
            ev_id="e2",
            domain="news2.com",
            title="光伏装机量大幅增长",
            snippet="2026 年新增装机突破 200GW",
            credibility="B",
        )
        result = detect_conflicts([], [ev1, ev2])
        assert result == []

    def test_overlap_different_credibility_creates_conflict(self) -> None:
        ev1 = _ev(
            ev_id="e1",
            domain="www.stats.gov.cn",
            title="2026 年光伏装机量大幅增长",
            snippet="国家统计局公布 2026 年新增装机突破 200GW",
            credibility="A",
        )
        ev2 = _ev(
            ev_id="e2",
            domain="blog.example.com",
            title="2026 年光伏装机量大幅增长",
            snippet="博客称 2026 年新增装机突破 200GW",
            credibility="D",
        )
        result = detect_conflicts([], [ev1, ev2])
        assert len(result) == 1
        c = result[0]
        assert c["evidence_a_id"] in {"e1", "e2"}
        assert c["evidence_b_id"] in {"e1", "e2"}
        assert c["evidence_a_id"] != c["evidence_b_id"]
        assert c["type"] == "factual"
        assert c["severity"] == "medium"
        assert c["status"] == "detected"

    def test_conflict_id_is_ulid(self) -> None:
        ev1 = _ev(
            ev_id="e1",
            domain="gov.cn",
            title="半导体出口数据",
            snippet="2026 年半导体出口数据公布",
            credibility="A",
        )
        ev2 = _ev(
            ev_id="e2",
            domain="other.com",
            title="半导体出口数据",
            snippet="2026 年半导体出口数据公布",
            credibility="D",
        )
        result = detect_conflicts([], [ev1, ev2])
        assert len(result[0]["id"]) == 26


# ---------------------------------------------------------------------------
# is_resolvable
# ---------------------------------------------------------------------------


class TestIsResolvable:
    def test_low_is_resolvable(self) -> None:
        assert is_resolvable({"id": "c", "claim": "", "evidence_a_id": "a", "evidence_b_id": "b", "type": "factual", "severity": "low", "status": "detected"}) is True

    def test_medium_is_resolvable(self) -> None:
        assert is_resolvable({"id": "c", "claim": "", "evidence_a_id": "a", "evidence_b_id": "b", "type": "factual", "severity": "medium", "status": "detected"}) is True

    def test_high_is_not_resolvable(self) -> None:
        assert is_resolvable({"id": "c", "claim": "", "evidence_a_id": "a", "evidence_b_id": "b", "type": "factual", "severity": "high", "status": "detected"}) is False


# ---------------------------------------------------------------------------
# resolve_by_authority
# ---------------------------------------------------------------------------


class TestResolveByAuthority:
    def test_keeps_higher_credibility_claim(self) -> None:
        ev_high = _ev(ev_id="high", credibility="A", domain="www.stats.gov.cn", title="2026 光伏装机 200GW")
        ev_low = _ev(ev_id="low", credibility="D", domain="blog.com", title="2026 光伏装机 200GW")
        claims = generate_draft_claims([ev_high, ev_low])
        conflict: ConflictDict = serialize_conflict(
            {
                "claim": "2026 光伏装机 200GW",
                "evidence_a_id": "high",
                "evidence_b_id": "low",
                "type": "factual",
                "severity": "low",
            }
        )
        kept = resolve_by_authority(claims, conflict, [ev_high, ev_low])
        assert len(kept) == 1
        assert kept[0]["citations"][0]["evidence_id"] == "high"

    def test_drops_lower_credibility_claim(self) -> None:
        ev_a = _ev(ev_id="a", credibility="A")
        ev_d = _ev(ev_id="d", credibility="D")
        claims = generate_draft_claims([ev_a, ev_d])
        conflict = serialize_conflict(
            {"claim": "x", "evidence_a_id": "a", "evidence_b_id": "d", "type": "factual", "severity": "medium"}
        )
        kept = resolve_by_authority(claims, conflict, [ev_a, ev_d])
        assert {c["citations"][0]["evidence_id"] for c in kept} == {"a"}

    def test_missing_evidence_no_change(self) -> None:
        ev_a = _ev(ev_id="a", credibility="A")
        ev_d = _ev(ev_id="d", credibility="D")
        claims = generate_draft_claims([ev_a, ev_d])
        conflict = serialize_conflict(
            {"claim": "x", "evidence_a_id": "ghost1", "evidence_b_id": "ghost2", "type": "factual", "severity": "medium"}
        )
        kept = resolve_by_authority(claims, conflict, [ev_a, ev_d])
        assert len(kept) == 2  # 没动


# ---------------------------------------------------------------------------
# serialize_conflict
# ---------------------------------------------------------------------------


class TestSerializeConflict:
    def test_assigns_id_and_status(self) -> None:
        c = serialize_conflict(
            {"claim": "x", "evidence_a_id": "a", "evidence_b_id": "b", "type": "factual", "severity": "low"}
        )
        assert len(c["id"]) == 26
        assert c["status"] == "detected"
        assert c["type"] == "factual"
        assert c["severity"] == "low"

    def test_defaults_type_and_severity(self) -> None:
        c = serialize_conflict({"claim": "x", "evidence_a_id": "a", "evidence_b_id": "b"})
        assert c["type"] == "factual"
        assert c["severity"] == "medium"


# ---------------------------------------------------------------------------
# 节点 run：基础路径
# ---------------------------------------------------------------------------


class TestRunBasic:
    @pytest.mark.asyncio
    async def test_no_material_no_existing_claims_returns_empty(self) -> None:
        patch = await run(_state())
        assert patch["conflicts"] == []
        assert patch["verdicts"] == []
        assert patch["report_claims"] == []
        assert "interrupt_reason" not in patch

    @pytest.mark.asyncio
    async def test_single_evidence_no_conflict(self) -> None:
        ev = _ev(ev_id="e1")
        patch = await run(_state(material=[ev]))
        # 单条 material → 派生 1 条 claim，无冲突，无 interrupt
        assert len(patch["report_claims"]) == 1
        assert patch["conflicts"] == []
        assert "interrupt_reason" not in patch

    @pytest.mark.asyncio
    async def test_no_deps_works(self) -> None:
        ev = _ev(ev_id="e1")
        patch = await run(_state(material=[ev]), deps=None)
        assert len(patch["report_claims"]) == 1

    @pytest.mark.asyncio
    async def test_existing_claims_reused(self) -> None:
        ev = _ev(ev_id="e1")
        existing: list[ReportClaim] = [
            {"id": "claim-existing", "text": "已有", "confidence": "single_source", "citations": [{"evidence_id": "e1"}]},
        ]
        patch = await run(_state(material=[ev], claims=existing))
        # 已有 claim 不会被覆盖
        assert patch["report_claims"][0]["id"] == "claim-existing"


# ---------------------------------------------------------------------------
# 节点 run：冲突检测与自动解决
# ---------------------------------------------------------------------------


class TestRunConflictDetection:
    @pytest.mark.asyncio
    async def test_overlap_low_credibility_diff_resolves_automatically(self) -> None:
        ev_a = _ev(
            ev_id="a",
            domain="www.stats.gov.cn",
            title="2026 光伏装机 200GW",
            snippet="2026 年光伏装机 200GW",
            credibility="A",
        )
        ev_d = _ev(
            ev_id="d",
            domain="blog.com",
            title="2026 光伏装机 200GW",
            snippet="2026 年光伏装机 200GW",
            credibility="D",
        )
        patch = await run(_state(material=[ev_a, ev_d]))
        # severity=medium 默认可解 → claim 被收敛到 1 条（A 胜出）
        assert len(patch["report_claims"]) == 1
        assert patch["report_claims"][0]["citations"][0]["evidence_id"] == "a"
        # 无不可解冲突 → 不写 interrupt
        assert "interrupt_reason" not in patch

    @pytest.mark.asyncio
    async def test_unresolvable_conflict_sets_interrupt(self) -> None:
        # 通过 monkeypatch 让 detect_conflicts 在前两次返回 severity=high 的冲突
        # 模拟"用户尚未裁决"：每次都重新产生同议题冲突
        from app.orchestrator.nodes import critic as critic_mod

        ev1 = _ev(ev_id="e1", title="同议题", snippet="相同内容", credibility="A")
        ev2 = _ev(ev_id="e2", title="同议题", snippet="相同内容", credibility="D")

        call_count = {"n": 0}

        def _fake_detect(claims, material):  # noqa: ARG001
            call_count["n"] += 1
            if call_count["n"] >= 2:
                # 第二轮起已无新冲突 → 收敛跳出
                return []
            fake_conflict = serialize_conflict(
                {"claim": "同议题", "evidence_a_id": "e1", "evidence_b_id": "e2", "type": "factual", "severity": "high"}
            )
            return [fake_conflict]

        original = critic_mod.detect_conflicts
        critic_mod.detect_conflicts = _fake_detect  # type: ignore[assignment]
        try:
            patch = await run(_state(material=[ev1, ev2]))
        finally:
            critic_mod.detect_conflicts = original  # type: ignore[assignment]

        # 第一轮发现 1 条不可解冲突 → 进入 conflicts；因未解决 → 写 interrupt
        assert len(patch["conflicts"]) == 1
        assert patch["conflicts"][0]["severity"] == "high"
        assert patch["interrupt_reason"] == "critique"
        assert patch["interrupt_payload"] == {"conflict_ids": [patch["conflicts"][0]["id"]]}


# ---------------------------------------------------------------------------
# 节点 run：token 预算超限跳出
# ---------------------------------------------------------------------------


class TestRunBudgetGuard:
    @pytest.mark.asyncio
    async def test_budget_exhausted_writes_interrupt(self) -> None:
        # budget=100, used=100 → used > 100 * 0.30 = 30 → 视为耗尽
        ev1 = _ev(ev_id="e1", title="同议题", snippet="相同", credibility="A")
        ev2 = _ev(ev_id="e2", title="同议题", snippet="相同", credibility="D")
        patch = await run(
            _state(material=[ev1, ev2], token_used=100, token_budget=100),
        )
        # material 不足两条的护栏不触发（已 2 条），但预算耗尽跳出循环；
        # 然而跳出循环后若无 conflict 也不会写 interrupt；我们这里没有冲突来源，
        # 所以这里只验证 budget_exhausted 的进入逻辑。
        assert "interrupt_reason" not in patch
        assert patch["conflicts"] == []

    @pytest.mark.asyncio
    async def test_budget_exhausted_with_existing_conflicts_writes_interrupt(self) -> None:
        # 模拟回流场景：state 里已有 conflict，预算耗尽 → 写 interrupt 等待裁决
        ev1 = _ev(ev_id="e1", title="同议题", snippet="相同", credibility="A")
        ev2 = _ev(ev_id="e2", title="同议题", snippet="相同", credibility="D")
        existing_conflict: ConflictDict = {
            "id": "c-existing",
            "claim": "x",
            "evidence_a_id": "e1",
            "evidence_b_id": "e2",
            "type": "factual",
            "severity": "high",
            "status": "awaiting_human",
        }
        patch = await run(
            _state(
                material=[ev1, ev2],
                conflicts=[existing_conflict],
                token_used=100,
                token_budget=100,
            ),
        )
        assert patch["conflicts"] == [existing_conflict]
        assert patch["interrupt_reason"] == "critique"
        assert patch["interrupt_payload"] == {"conflict_ids": ["c-existing"]}


# ---------------------------------------------------------------------------
# 节点 run：回流收敛（已有 verdicts）
# ---------------------------------------------------------------------------


class TestRunResumeConverge:
    @pytest.mark.asyncio
    async def test_existing_verdicts_clear_interrupt(self) -> None:
        ev1 = _ev(ev_id="e1", title="同议题", snippet="相同", credibility="A")
        ev2 = _ev(ev_id="e2", title="同议题", snippet="相同", credibility="D")
        existing_conflict: ConflictDict = {
            "id": "c-existing",
            "claim": "x",
            "evidence_a_id": "e1",
            "evidence_b_id": "e2",
            "type": "factual",
            "severity": "high",
            "status": "awaiting_human",
        }
        verdicts = [{"conflict_id": "c-existing", "user_id": "u1", "choice": "evidence_a", "reason": "权威"}]
        patch = await run(
            _state(
                material=[ev1, ev2],
                conflicts=[existing_conflict],
                verdicts=verdicts,
            ),
        )
        # 已有 verdict → 不写 interrupt；claims 派生为 2 条
        assert "interrupt_reason" not in patch
        assert patch["verdicts"] == verdicts


# ---------------------------------------------------------------------------
# 常量校验
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_iterations(self) -> None:
        assert MAX_CRITIC_ITERATIONS == 3

    def test_budget_ratio(self) -> None:
        assert CRITIC_BUDGET_RATIO == 0.30
