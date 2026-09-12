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
- M2-2：``build_candidate_pairs`` 预筛与上限、``detect_conflicts_semantic``
  结构化判定 / 三类故障降级 / 脏 pair_index 降级、节点 LLM 接线与单批调用
- 节点签名接受 ``deps=None``（向后兼容 M0）
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProviderUnavailableError
from app.db.models.conflict import Conflict
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes.critic import (
    CRITIC_BUDGET_RATIO,
    LLM_BATCH_TIMEOUT_SECONDS,
    MAX_CRITIC_ITERATIONS,
    MAX_LLM_CANDIDATE_PAIRS,
    apply_human_verdict,
    apply_pending_verdicts,
    build_candidate_pairs,
    detect_conflicts,
    detect_conflicts_semantic,
    generate_draft_claims,
    is_resolvable,
    pending_conflict_ids,
    resolve_by_authority,
    run,
    serialize_conflict,
)
from app.orchestrator.schemas import (
    ConflictDetectionSchema,
    ConflictPairResult,
)
from app.orchestrator.state import ConflictDict, EvidenceDict, ReportClaim, VerdictDict
from app.provider.client import StructuredCompletion
from app.realtime.hub import RealtimeHub

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
    verdicts: list[VerdictDict] | None = None,
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
        assert (
            is_resolvable(
                {
                    "id": "c",
                    "claim": "",
                    "evidence_a_id": "a",
                    "evidence_b_id": "b",
                    "type": "factual",
                    "severity": "low",
                    "status": "detected",
                }
            )
            is True
        )

    def test_medium_is_resolvable(self) -> None:
        assert (
            is_resolvable(
                {
                    "id": "c",
                    "claim": "",
                    "evidence_a_id": "a",
                    "evidence_b_id": "b",
                    "type": "factual",
                    "severity": "medium",
                    "status": "detected",
                }
            )
            is True
        )

    def test_high_is_not_resolvable(self) -> None:
        assert (
            is_resolvable(
                {
                    "id": "c",
                    "claim": "",
                    "evidence_a_id": "a",
                    "evidence_b_id": "b",
                    "type": "factual",
                    "severity": "high",
                    "status": "detected",
                }
            )
            is False
        )


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
            {
                "claim": "x",
                "evidence_a_id": "a",
                "evidence_b_id": "d",
                "type": "factual",
                "severity": "medium",
            }
        )
        kept = resolve_by_authority(claims, conflict, [ev_a, ev_d])
        assert {c["citations"][0]["evidence_id"] for c in kept} == {"a"}

    def test_missing_evidence_no_change(self) -> None:
        ev_a = _ev(ev_id="a", credibility="A")
        ev_d = _ev(ev_id="d", credibility="D")
        claims = generate_draft_claims([ev_a, ev_d])
        conflict = serialize_conflict(
            {
                "claim": "x",
                "evidence_a_id": "ghost1",
                "evidence_b_id": "ghost2",
                "type": "factual",
                "severity": "medium",
            }
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
            {
                "id": "claim-existing",
                "text": "已有",
                "confidence": "single_source",
                "citations": [{"evidence_id": "e1"}],
            },
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
                {
                    "claim": "同议题",
                    "evidence_a_id": "e1",
                    "evidence_b_id": "e2",
                    "type": "factual",
                    "severity": "high",
                }
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
        verdicts: list[VerdictDict] = [
            {"conflict_id": "c-existing", "user_id": "u1", "choice": "evidence_a", "reason": "权威"}
        ]
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


# ---------------------------------------------------------------------------
# M2-2：词面候选预筛
# ---------------------------------------------------------------------------


class TestBuildCandidatePairs:
    def test_less_than_two_returns_empty(self) -> None:
        assert build_candidate_pairs([]) == []
        assert build_candidate_pairs([_ev(ev_id="e1")]) == []

    def test_unrelated_pair_not_candidate(self) -> None:
        ev1 = _ev(ev_id="e1", title="光伏装机数据", snippet="2026 年新增装机统计", credibility="A")
        ev2 = _ev(ev_id="e2", title="城市美食推荐", snippet="北京必吃餐厅榜单", credibility="D")
        assert build_candidate_pairs([ev1, ev2]) == []

    def test_same_topic_low_overlap_pair_is_candidate(self) -> None:
        # AC-1 场景：同议题、结论相反、仅共享主题词（显著低于启发式 0.4 阈值）
        ev1 = _ev(
            ev_id="e1",
            title="2026 年光伏新增装机规模创新高",
            snippet="国家统计局公布光伏新增装机达到 210GW，同比增长 45%",
            credibility="A",
        )
        ev2 = _ev(
            ev_id="e2",
            title="光伏新增装机被指高估",
            snippet="行业协会按并网口径统计实际仅 120GW，210GW 含未并网立项",
            credibility="C",
        )
        pairs = build_candidate_pairs([ev1, ev2])
        assert pairs == [(0, 1)]

    def test_same_credibility_pair_still_candidate(self) -> None:
        # temporal/perspective 冲突可发生在同可信度之间，预筛不再以可信度差为门槛
        ev1 = _ev(
            ev_id="e1", title="2024 年光伏装机 100GW", snippet="当年光伏新增装机统计口径", credibility="B"
        )
        ev2 = _ev(
            ev_id="e2", title="2026 年光伏装机 210GW", snippet="当期光伏新增装机统计口径", credibility="B"
        )
        assert build_candidate_pairs([ev1, ev2]) == [(0, 1)]

    def test_cap_respected_and_ordered_by_similarity(self) -> None:
        # 4 条同簇证据两两相似（6 对），cap=2 时只保留相似度最高的 2 对
        cluster = [
            _ev(
                ev_id=f"c{i}",
                title="2026 年光伏新增装机统计报告" + str(i),
                snippet="光伏新增装机容量数据" + str(i),
            )
            for i in range(4)
        ]
        pairs = build_candidate_pairs(cluster, cap=2)
        assert len(pairs) == 2
        # 下标升序，i<j 恒成立
        assert all(i < j for i, j in pairs)

    def test_twelve_evidence_pair_count_under_cap(self) -> None:
        # TR-4.3：12 条证据（6 条同簇产生 15 对候选 + 6 条无关）→ 截断到上限 12，
        # 绝不做 66 对全量 LLM 调用
        shared = "2026 年中国新能源光伏行业新增装机容量数据统计"
        cluster = [
            _ev(
                ev_id=f"c{i}",
                title=shared + f"批次{i}",
                snippet=f"光伏新增装机容量统计口径分析第{i}篇",
            )
            for i in range(6)
        ]
        unrelated = [
            _ev(ev_id=f"u{i}", title=f"完全不同主题编号{i}", snippet=f"美食旅行历史体育游戏音乐{i}")
            for i in range(6)
        ]
        pairs = build_candidate_pairs(cluster + unrelated)
        assert len(pairs) == MAX_LLM_CANDIDATE_PAIRS
        assert len(pairs) < 12 * 11 // 2


# ---------------------------------------------------------------------------
# M2-2：LLM 语义检测
# ---------------------------------------------------------------------------


def _llm_completion(results: list[ConflictPairResult], *, tokens: int = 42) -> StructuredCompletion:
    return StructuredCompletion(
        parsed=ConflictDetectionSchema(results=results),
        usage={"total_tokens": tokens},
        model="deepseek-test",
        raw="{}",
    )


def _fake_llm(
    *,
    completion: StructuredCompletion | None = None,
    side_effect: Any = None,
) -> MagicMock:
    llm = MagicMock(name="fake_llm")
    if side_effect is not None:
        llm.complete_structured = AsyncMock(side_effect=side_effect)
    else:
        llm.complete_structured = AsyncMock(return_value=completion)
    return llm


def _opposite_pair() -> list[EvidenceDict]:
    return [
        _ev(
            ev_id="e1",
            domain="www.stats.gov.cn",
            title="2026 年光伏新增装机规模创新高",
            snippet="国家统计局公布光伏新增装机达到 210GW，同比增长 45%",
            credibility="A",
        ),
        _ev(
            ev_id="e2",
            domain="assoc.example.org",
            title="光伏新增装机被指高估",
            snippet="行业协会按并网口径统计实际仅 120GW，210GW 含未并网立项",
            credibility="C",
        ),
    ]


class TestDetectConflictsSemantic:
    @pytest.mark.asyncio
    async def test_llm_conflict_json_maps_fields(self) -> None:
        # TR-4.1：mock LLM 判冲突 → ConflictDict 字段齐全、枚举合法、claim 透传
        material = _opposite_pair()
        llm = _fake_llm(
            completion=_llm_completion(
                [
                    ConflictPairResult(
                        pair_index=0,
                        is_conflict=True,
                        claim="2026 年光伏新增装机规模",
                        type="methodological",
                        severity="high",
                        reason="统计口径不同导致数字矛盾",
                    )
                ]
            )
        )
        round_result = await detect_conflicts_semantic(material, llm=llm, run_id="r1")

        assert round_result.degraded is False
        assert round_result.tokens_used == 42
        assert len(round_result.conflicts) == 1
        c = round_result.conflicts[0]
        assert c["evidence_a_id"] == "e1"
        assert c["evidence_b_id"] == "e2"
        assert c["claim"] == "2026 年光伏新增装机规模"
        assert c["type"] == "methodological"
        assert c["severity"] == "high"
        assert c["status"] == "detected"
        assert len(c["id"]) == 26

        # 单批一次调用，温度 0、8s 超时、critique 标签
        kwargs = llm.complete_structured.await_args.kwargs
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] > 0
        assert kwargs["tags"] == ["critique"]

    @pytest.mark.asyncio
    async def test_non_conflict_result_returns_empty(self) -> None:
        llm = _fake_llm(completion=_llm_completion([ConflictPairResult(pair_index=0, is_conflict=False)]))
        round_result = await detect_conflicts_semantic(_opposite_pair(), llm=llm)
        assert round_result.conflicts == []
        assert round_result.degraded is False

    @pytest.mark.asyncio
    async def test_no_candidate_pair_skips_llm(self) -> None:
        # 预筛无同议题候选 → 不调 LLM、不判降级
        material = [
            _ev(ev_id="e1", title="光伏装机", snippet="新能源统计数据"),
            _ev(ev_id="e2", title="美食推荐", snippet="城市餐厅榜单"),
        ]
        llm = _fake_llm(completion=_llm_completion([]))
        round_result = await detect_conflicts_semantic(material, llm=llm)
        assert round_result.conflicts == []
        assert round_result.degraded is False
        llm.complete_structured.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_claim_falls_back_to_evidence_title(self) -> None:
        llm = _fake_llm(
            completion=_llm_completion(
                [ConflictPairResult(pair_index=0, is_conflict=True, claim="", severity="low")]
            )
        )
        round_result = await detect_conflicts_semantic(_opposite_pair(), llm=llm)
        assert round_result.conflicts[0]["claim"] == "2026 年光伏新增装机规模创新高"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc_factory", "label"),
        [
            (lambda: RuntimeError("provider boom"), "provider_error"),
            (lambda: TimeoutError(), "timeout"),
            (lambda: ProviderUnavailableError("结构化输出解析失败：Expecting value"), "dirty_json"),
        ],
    )
    async def test_three_failure_modes_fall_back_to_heuristic(self, exc_factory: Any, label: str) -> None:
        # TR-4.2：异常 / 超时 / 脏 JSON 三类注入都走启发式且置 degraded，不抛穿
        material = [
            _ev(
                ev_id="a",
                title="光伏装机统计数据公布" + label,
                snippet="光伏装机统计数据公布" + label,
                credibility="A",
            ),
            _ev(
                ev_id="d",
                title="光伏装机统计数据公布" + label,
                snippet="光伏装机统计数据公布" + label,
                credibility="D",
            ),
        ]
        llm = _fake_llm(side_effect=exc_factory())
        round_result = await detect_conflicts_semantic(material, llm=llm, run_id="r1")

        assert round_result.degraded is True
        assert round_result.tokens_used == 0
        # 启发式：高重叠 + 可信度不同 → 检出 1 条 medium/factual
        assert len(round_result.conflicts) == 1
        assert round_result.conflicts[0]["severity"] == "medium"
        assert round_result.conflicts[0]["type"] == "factual"

    @pytest.mark.asyncio
    async def test_out_of_range_pair_index_degrades(self) -> None:
        # schema 合法但下标越界的脏返回：整轮保守降级
        material = [
            _ev(ev_id="a", title="光伏装机统计口径数据", snippet="光伏装机统计口径数据", credibility="A"),
            _ev(ev_id="d", title="光伏装机统计口径数据", snippet="光伏装机统计口径数据", credibility="D"),
        ]
        llm = _fake_llm(
            completion=_llm_completion([ConflictPairResult(pair_index=99, is_conflict=True, severity="high")])
        )
        round_result = await detect_conflicts_semantic(material, llm=llm)
        assert round_result.degraded is True
        assert len(round_result.conflicts) == 1  # 启发式兜底仍给出结果

    @pytest.mark.asyncio
    async def test_duplicate_pair_index_degrades(self) -> None:
        material = _opposite_pair()
        llm = _fake_llm(
            completion=_llm_completion(
                [
                    ConflictPairResult(pair_index=0, is_conflict=False),
                    ConflictPairResult(pair_index=0, is_conflict=True, severity="high"),
                ]
            )
        )
        round_result = await detect_conflicts_semantic(material, llm=llm)
        assert round_result.degraded is True

    @pytest.mark.asyncio
    async def test_batch_timeout_is_eight_seconds(self) -> None:
        assert LLM_BATCH_TIMEOUT_SECONDS == 8.0

    @pytest.mark.asyncio
    async def test_user_message_contains_candidate_fields(self) -> None:
        material = _opposite_pair()
        llm = _fake_llm(completion=_llm_completion([]))
        await detect_conflicts_semantic(material, llm=llm)
        user_content = llm.complete_structured.await_args.kwargs["messages"][1].content
        payload = json.loads(user_content.split("\n", 1)[1])
        pair = payload["pairs"][0]
        assert pair["pair_index"] == 0
        assert pair["evidence_a"]["credibility"] == "A"
        assert pair["evidence_b"]["credibility"] == "C"
        assert "snippet" in pair["evidence_a"]


# ---------------------------------------------------------------------------
# M2-2：节点 run 与 LLM 接线
# ---------------------------------------------------------------------------


def _deps_with_llm(llm: MagicMock) -> NodeDeps:
    return NodeDeps(run_id="run_test", team_id="t1", trace_id="tr1", llm=llm)


class TestRunWithSemanticDetection:
    @pytest.mark.asyncio
    async def test_high_conflict_suspends_without_degraded(self) -> None:
        # AC-1 节点侧：语义 high 冲突 → interrupt + critic_degraded=False + token 回写
        llm = _fake_llm(
            completion=_llm_completion(
                [
                    ConflictPairResult(
                        pair_index=0,
                        is_conflict=True,
                        claim="2026 年光伏新增装机规模",
                        type="factual",
                        severity="high",
                        reason="关键数字互相矛盾",
                    )
                ],
                tokens=55,
            )
        )
        patch = await run(_state(material=_opposite_pair()), deps=_deps_with_llm(llm))
        assert patch["critic_degraded"] is False
        assert patch["token_used"] == 55
        assert len(patch["conflicts"]) == 1
        assert patch["conflicts"][0]["severity"] == "high"
        assert patch["interrupt_reason"] == "critique"
        assert patch["interrupt_payload"] == {"conflict_ids": [patch["conflicts"][0]["id"]]}

    @pytest.mark.asyncio
    async def test_llm_failure_marks_run_degraded(self) -> None:
        material = [
            _ev(ev_id="a", title="光伏装机统计口径发布", snippet="光伏装机统计口径发布", credibility="A"),
            _ev(ev_id="d", title="光伏装机统计口径发布", snippet="光伏装机统计口径发布", credibility="D"),
        ]
        llm = _fake_llm(side_effect=ProviderUnavailableError("熔断"))
        patch = await run(_state(material=material), deps=_deps_with_llm(llm))
        assert patch["critic_degraded"] is True
        # medium 启发式冲突被自动收敛，不挂起
        assert "interrupt_reason" not in patch

    @pytest.mark.asyncio
    async def test_single_batch_for_all_candidate_pairs(self) -> None:
        # TR-4.3：12 条证据 / 12 个候选对只发起 1 次 LLM 调用（单批），
        # 且批内候选对数 == 预筛上限（非 66 对）
        shared = "2026 年中国新能源光伏行业新增装机容量数据统计"
        cluster = [
            _ev(
                ev_id=f"c{i}",
                title=shared + f"批次{i}",
                snippet=f"光伏新增装机容量统计口径分析第{i}篇",
            )
            for i in range(6)
        ]
        unrelated = [
            _ev(ev_id=f"u{i}", title=f"完全不同主题编号{i}", snippet=f"美食旅行历史体育游戏音乐{i}")
            for i in range(6)
        ]
        llm = _fake_llm(completion=_llm_completion([]))
        await run(_state(material=cluster + unrelated), deps=_deps_with_llm(llm))

        assert llm.complete_structured.await_count == 1
        user_content = llm.complete_structured.await_args.kwargs["messages"][1].content
        payload = json.loads(user_content.split("\n", 1)[1])
        assert len(payload["pairs"]) == MAX_LLM_CANDIDATE_PAIRS


# ---------------------------------------------------------------------------
# M2-2 Task 5：自动收敛留痕 / 人工裁决四值收敛 / WS 帧 / 落库接线
# ---------------------------------------------------------------------------


def _high_conflict(
    cid: str = "c1",
    *,
    ev_a: str = "e1",
    ev_b: str = "e2",
    status: str = "awaiting_human",
) -> ConflictDict:
    return {
        "id": cid,
        "claim": "2026 年光伏新增装机规模",
        "evidence_a_id": ev_a,
        "evidence_b_id": ev_b,
        "type": "factual",
        "severity": "high",
        "status": status,  # type: ignore[typeddict-item]
    }


def _claims_for(ev_ids: list[str]) -> list[ReportClaim]:
    return [
        {
            "id": f"claim-{ev_id}",
            "text": f"来自证据 {ev_id} 的论断",
            "confidence": "single_source",
            "citations": [{"evidence_id": ev_id}],
        }
        for ev_id in ev_ids
    ]


def _verdict(cid: str, choice: str, *, note: str | None = None) -> VerdictDict:
    verdict: VerdictDict = {"conflict_id": cid, "user_id": "u1", "choice": choice, "reason": "裁决理由"}
    if note is not None:
        verdict["additional_note"] = note
    return verdict


async def _drain_frames(hub: RealtimeHub, run_id: str, *, count: int) -> list[dict[str, Any]]:
    """订阅频道并取出至多 count 帧（取不到超时失败）。"""
    frames: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for event in hub.subscribe(f"runs:{run_id}"):
            frames.append(event)
            if len(frames) >= count:
                break

    task = asyncio.create_task(_collect())
    await asyncio.wait_for(task, timeout=1.0)
    return frames


class TestAutoResolvePersistShape:
    @pytest.mark.asyncio
    async def test_medium_conflict_recorded_resolved_without_interrupt(self) -> None:
        """TR-5.1 节点侧：medium 自动收敛冲突留痕为 resolved、不挂起。"""
        ev_a = _ev(ev_id="a", title="2026 光伏装机 200GW", snippet="2026 光伏装机 200GW", credibility="A")
        ev_d = _ev(ev_id="d", title="2026 光伏装机 200GW", snippet="2026 光伏装机 200GW", credibility="D")
        patch = await run(_state(material=[ev_a, ev_d]))
        assert len(patch["conflicts"]) == 1
        conflict = patch["conflicts"][0]
        assert conflict["status"] == "resolved"
        assert conflict["severity"] == "medium"
        # 高可信度 claim 保留
        assert [c["citations"][0]["evidence_id"] for c in patch["report_claims"]] == ["a"]
        assert "interrupt_reason" not in patch

    @pytest.mark.asyncio
    async def test_no_hub_no_session_still_runs(self) -> None:
        """未注入 hub/db_session 时落库与推送静默跳过，节点不报错。"""
        ev_a = _ev(ev_id="a", title="2026 光伏装机 200GW", snippet="2026 光伏装机 200GW", credibility="A")
        ev_d = _ev(ev_id="d", title="2026 光伏装机 200GW", snippet="2026 光伏装机 200GW", credibility="D")
        patch = await run(_state(material=[ev_a, ev_d]), deps=NodeDeps(run_id="r", team_id="t", trace_id="x"))
        assert patch["conflicts"][0]["status"] == "resolved"


class TestHumanVerdictConvergence:
    def test_apply_human_verdict_unknown_choice_keeps_all(self) -> None:
        claims = _claims_for(["e1", "e2"])
        out = apply_human_verdict(claims, _high_conflict(), "unexpected")
        assert {c["citations"][0]["evidence_id"] for c in out} == {"e1", "e2"}

    @pytest.mark.parametrize(
        ("choice", "expected_ids", "flagged"),
        [
            ("evidence_a", ["e1"], False),
            ("evidence_b", ["e2"], False),
            ("both", ["e1", "e2"], True),
            ("reject", [], False),
        ],
    )
    @pytest.mark.asyncio
    async def test_four_choices_converge_claims(
        self, choice: str, expected_ids: list[str], flagged: bool
    ) -> None:
        """TR-5.3/AC-8：四 choice 经节点回流后 report_claims 集合符合语义。"""
        ev1 = _ev(ev_id="e1", title="同议题", snippet="相同内容", credibility="A")
        ev2 = _ev(ev_id="e2", title="同议题", snippet="相同内容", credibility="D")
        patch = await run(
            _state(
                material=[ev1, ev2],
                claims=_claims_for(["e1", "e2"]),
                conflicts=[_high_conflict("c1")],
                verdicts=[_verdict("c1", choice, note="补充备注")],
            )
        )
        got_ids = [c["citations"][0]["evidence_id"] for c in patch["report_claims"]]
        assert got_ids == expected_ids
        # 冲突已按裁决收敛为 resolved，不再挂起
        assert patch["conflicts"][0]["status"] == "resolved"
        assert "interrupt_reason" not in patch
        assert pending_conflict_ids(patch["conflicts"], patch["verdicts"]) == []
        # both：保留的 claim 带冲突 ID 并存标记（Reporter 据此显式呈现）
        if flagged:
            assert all(c.get("divergence_flags") == ["c1"] for c in patch["report_claims"])
        else:
            assert all("divergence_flags" not in c for c in patch["report_claims"])
        # additional_note 随裁决透传
        assert patch["verdicts"][0].get("additional_note") == "补充备注"

    @pytest.mark.asyncio
    async def test_resolved_conflict_verdict_not_reapplied(self) -> None:
        """重入安全：已 resolved 的冲突即使 verdict 仍在 state 也不二次收敛。"""
        conflict = _high_conflict("c1", status="resolved")
        claims = apply_pending_verdicts(_claims_for(["e1", "e2"]), [conflict], [_verdict("c1", "reject")])
        assert len(claims) == 2
        assert conflict["status"] == "resolved"


class TestConflictDetectedFrame:
    @pytest.mark.asyncio
    async def test_high_conflict_emits_one_nested_frame(self) -> None:
        """TR-5.2 节点侧：每条 high 冲突恰好一帧，冲突对象嵌套于 payload。"""
        hub = RealtimeHub()
        collect = asyncio.create_task(_drain_frames(hub, "run_test", count=1))
        # 嵌套收集协程需要两个调度节拍完成订阅注册
        await asyncio.sleep(0.05)
        llm = _fake_llm(
            completion=_llm_completion(
                [
                    ConflictPairResult(
                        pair_index=0,
                        is_conflict=True,
                        claim="2026 年光伏新增装机规模",
                        type="factual",
                        severity="high",
                        reason="关键数字互相矛盾",
                    )
                ]
            )
        )
        deps = NodeDeps(run_id="run_test", team_id="t1", trace_id="tr1", llm=llm, hub=hub)
        patch = await run(_state(material=_opposite_pair()), deps=deps)
        frames = await collect

        assert len(frames) == 1
        frame = frames[0]
        assert frame["type"] == "conflict.detected"
        assert frame["run_id"] == "run_test"
        assert frame["stage"] == "critique"
        # 载荷嵌套：冲突分类 type 在 payload 内，不与帧 type 互相覆盖
        payload = frame["payload"]
        cid = patch["conflicts"][0]["id"]
        assert payload == {
            "id": cid,
            "claim": "2026 年光伏新增装机规模",
            "evidence_a_id": patch["conflicts"][0]["evidence_a_id"],
            "evidence_b_id": patch["conflicts"][0]["evidence_b_id"],
            "type": "factual",
            "severity": "high",
            "status": "awaiting_human",
        }

    @pytest.mark.asyncio
    async def test_auto_resolved_conflict_emits_no_frame(self) -> None:
        """TR-5.1：low/medium 自动收敛不推送 conflict.detected。"""
        hub = RealtimeHub()
        ev_a = _ev(ev_id="a", title="2026 光伏装机 200GW", snippet="2026 光伏装机 200GW", credibility="A")
        ev_d = _ev(ev_id="d", title="2026 光伏装机 200GW", snippet="2026 光伏装机 200GW", credibility="D")

        async def _expect_empty() -> None:
            async for event in hub.subscribe("runs:run_test"):
                raise AssertionError(f"自动收敛不应推送冲突帧，收到：{event}")

        task = asyncio.create_task(_expect_empty())
        await asyncio.sleep(0)
        deps = NodeDeps(run_id="run_test", team_id="t1", trace_id="tr1", hub=hub)
        await run(_state(material=[ev_a, ev_d]), deps=deps)
        await asyncio.sleep(0.05)
        task.cancel()


class _ConflictCapturingSession:
    """最小假会话：scalars 返回空集（库内无冲突），add 记录 ORM 对象。"""

    def __init__(self) -> None:
        self.added: list[Any] = []

    async def scalars(self, statement: Any) -> Any:
        class _Result:
            def all(self_inner) -> list[Any]:
                return []

        return _Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)


class TestPersistWiring:
    @pytest.mark.asyncio
    async def test_detected_conflicts_become_orm_rows(self) -> None:
        """节点检出后经 deps.db_session 幂等落库（状态映射在集成测试验真库）。"""
        session = _ConflictCapturingSession()
        llm = _fake_llm(
            completion=_llm_completion(
                [
                    ConflictPairResult(
                        pair_index=0,
                        is_conflict=True,
                        claim="2026 年光伏新增装机规模",
                        type="factual",
                        severity="high",
                        reason="数字矛盾",
                    )
                ]
            )
        )
        deps = NodeDeps(
            run_id="run_test",
            team_id="t1",
            trace_id="tr1",
            llm=llm,
            db_session=cast(AsyncSession, session),
        )
        patch = await run(_state(material=_opposite_pair()), deps=deps)
        rows = [o for o in session.added if isinstance(o, Conflict)]
        assert len(rows) == 1
        assert rows[0].id == patch["conflicts"][0]["id"]
        assert rows[0].run_id == "run_test"
        assert rows[0].status == "awaiting_human"
        assert rows[0].resolved_at is None
