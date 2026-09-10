"""WP-5.4 researcher_fan_out 节点单元测试。

覆盖：
- 档位 → top_k 映射
- 拓扑分层（无依赖 / 链式依赖 / 环路兜底 / 空输入）
- 节点 run：空 sub_questions / 全部已终止（幂等） / LLM 命中 + 成功 / 失败隔离 / 跨层顺序
- EvidenceDict 字段契约（id / sub_question_id / fingerprint / source_level=C 等占位）
- 域抽取（去 www.）
- 节点签名接受 ``deps=None``（向后兼容 M0）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes.researcher_fan_out import (
    run,
    top_k_for_tier,
    topological_layers_for_sub_questions,
)
from app.orchestrator.state import SubQuestionDict
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource

# ---------------------------------------------------------------------------
# 替身：满足 RetrievalClient 协议的可控客户端
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeRetrievalClient:
    """可预设每条 ``RetrievalRequest.query`` 返回 ``search_hits`` 的检索替身。"""

    search_hits: dict[str, list[RetrievalHit]] = field(default_factory=dict)
    search_calls: list[RetrievalRequest] = field(default_factory=list)
    extract_calls: list[list[RetrievalHit]] = field(default_factory=list)
    search_delay: float = 0.0
    search_raises: dict[str, BaseException] = field(default_factory=dict)
    extract_raises: dict[str, BaseException] = field(default_factory=dict)
    empty_extract: bool = False

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        self.search_calls.append(request)
        if request.query in self.search_raises:
            raise self.search_raises[request.query]
        if self.search_delay > 0:
            import asyncio

            await asyncio.sleep(self.search_delay)
        return list(self.search_hits.get(request.query, []))

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        self.extract_calls.append(list(hits))
        if hits and hits[0].url in self.extract_raises:
            raise self.extract_raises[hits[0].url]
        if self.empty_extract:
            return [RetrievalHit(**{**hits[0].__dict__, "content": None})] if hits else []
        return list(hits)

    async def aclose(self) -> None:  # pragma: no cover - 占位
        return None


def _make_hit(
    *,
    url: str,
    title: str = "t",
    snippet: str = "s",
    content: str | None = "body",
    score: float = 0.5,
    published_at: datetime | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        source=RetrievalSource.WEB,
        title=title,
        url=url,
        snippet=snippet,
        score=score,
        content=content,
        published_at=published_at,
        fetched_at=datetime.now(tz=UTC),
    )


def _make_subq(
    subq_id: str,
    question: str = "调研 A",
    *,
    depends_on: list[str] | None = None,
    status: str = "pending",
) -> SubQuestionDict:
    return {
        "id": subq_id,
        "question": question,
        "depends_on": depends_on or [],
        "status": status,  # type: ignore[typeddict-item]
        "evidence_ids": [],
    }


def _state_with(subqs: list[SubQuestionDict], *, tier: str = "standard") -> dict[str, Any]:
    return {
        "run_id": "run_test_01",
        "tier": tier,
        "sub_questions": subqs,
        "evidence": [],
    }


def _deps_with(client: _FakeRetrievalClient | None) -> NodeDeps:
    """构造只含检索客户端的 NodeDeps（其余字段填默认）。"""
    return NodeDeps(
        run_id="run_test_01",
        team_id="team_test",
        trace_id="trace_test",
        retrieval_client=client,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 档位 → top_k
# ---------------------------------------------------------------------------


class TestTopKForTier:
    def test_quick(self) -> None:
        assert top_k_for_tier("quick") == 3

    def test_standard(self) -> None:
        assert top_k_for_tier("standard") == 5

    def test_deep(self) -> None:
        assert top_k_for_tier("deep") == 8

    def test_extreme(self) -> None:
        assert top_k_for_tier("extreme") == 10

    def test_unknown_defaults_to_standard(self) -> None:
        assert top_k_for_tier("ultra") == 5

    def test_none_defaults_to_standard(self) -> None:
        assert top_k_for_tier(None) == 5


# ---------------------------------------------------------------------------
# 拓扑分层
# ---------------------------------------------------------------------------


class TestTopologicalLayers:
    def test_empty(self) -> None:
        assert topological_layers_for_sub_questions([]) == []

    def test_no_dependencies_single_layer(self) -> None:
        sq1 = _make_subq("a")
        sq2 = _make_subq("b")
        sq3 = _make_subq("c")
        layers = topological_layers_for_sub_questions([sq1, sq2, sq3])
        assert len(layers) == 1
        assert {s["id"] for s in layers[0]} == {"a", "b", "c"}

    def test_chain(self) -> None:
        # a 无依赖，b 依赖 a，c 依赖 b → 3 层
        a = _make_subq("a")
        b = _make_subq("b", depends_on=["a"])
        c = _make_subq("c", depends_on=["b"])
        layers = topological_layers_for_sub_questions([a, b, c])
        assert [[s["id"] for s in layer] for layer in layers] == [["a"], ["b"], ["c"]]

    def test_diamond(self) -> None:
        # a 无依赖；b, c 依赖 a；d 依赖 b, c
        a = _make_subq("a")
        b = _make_subq("b", depends_on=["a"])
        c = _make_subq("c", depends_on=["a"])
        d = _make_subq("d", depends_on=["b", "c"])
        layers = topological_layers_for_sub_questions([a, b, c, d])
        assert [[s["id"] for s in layer] for layer in layers] == [
            ["a"],
            ["b", "c"],
            ["d"],
        ]

    def test_cycle_fallback(self) -> None:
        a = _make_subq("a", depends_on=["b"])
        b = _make_subq("b", depends_on=["a"])
        layers = topological_layers_for_sub_questions([a, b])
        # 防御性兜底：整个环并入一层
        assert len(layers) == 1
        assert {s["id"] for s in layers[0]} == {"a", "b"}


# ---------------------------------------------------------------------------
# 节点 run：基础路径
# ---------------------------------------------------------------------------


class TestRunBasic:
    @pytest.mark.asyncio
    async def test_no_sub_questions_returns_empty(self) -> None:
        client = _FakeRetrievalClient()
        patch = await run(_state_with([]), deps=_deps_with(client))
        # instrument 会注入 current_stage + updated_at
        assert patch.get("evidence") == []
        assert client.search_calls == []

    @pytest.mark.asyncio
    async def test_all_terminal_idempotent(self) -> None:
        client = _FakeRetrievalClient()
        subqs = [
            _make_subq("a", status="succeeded"),
            _make_subq("b", status="failed"),
        ]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert patch.get("evidence") == []
        assert client.search_calls == []

    @pytest.mark.asyncio
    async def test_no_deps_uses_default_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """deps=None 时节点尝试解析全局客户端；解析抛错时全部 pending 子问题标记 failed。"""
        from app.orchestrator.nodes import researcher_fan_out as node_mod

        def boom() -> None:
            raise RuntimeError("no provider")

        monkeypatch.setattr(node_mod, "get_default_client", boom)

        subqs = [_make_subq("a"), _make_subq("b")]
        patch = await run(_state_with(subqs), deps=None)
        statuses = {s["status"] for s in patch.get("sub_questions", [])}
        assert statuses == {"failed"}
        assert patch.get("evidence") == []


# ---------------------------------------------------------------------------
# 节点 run：检索成功路径
# ---------------------------------------------------------------------------


class TestRunSuccessful:
    @pytest.mark.asyncio
    async def test_single_sub_question_with_hits(self) -> None:
        client = _FakeRetrievalClient(
            search_hits={
                "调研 A": [
                    _make_hit(url="https://example.com/a", title="A", snippet="snippet A"),
                ]
            }
        )
        subqs = [_make_subq("a", question="调研 A")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))

        # 状态子问题更新为 succeeded
        updated_subs = {s["id"]: s for s in patch["sub_questions"]}
        assert updated_subs["a"]["status"] == "succeeded"
        assert len(updated_subs["a"]["evidence_ids"]) == 1

        # EvidenceDict 字段契约
        ev = patch["evidence"][0]
        assert ev["sub_question_id"] == "a"
        assert ev["url"] == "https://example.com/a"
        assert ev["domain"] == "example.com"
        assert ev["title"] == "A"
        assert ev["snippet"] == "snippet A"
        assert ev["source_type"] == "news"
        # source_level / credibility 留给 standardizer
        assert ev["source_level"] == "tertiary"
        assert ev["credibility"] == "C"
        assert isinstance(ev["fingerprint"], str) and len(ev["fingerprint"]) == 64
        assert ev["published_at"] is None  # hit 未设置
        assert isinstance(ev["fetched_at"], str)

        # 检索调用记录
        assert len(client.search_calls) == 1
        assert client.search_calls[0].query == "调研 A"
        assert client.search_calls[0].top_k == 5  # standard 档位

    @pytest.mark.asyncio
    async def test_quick_uses_top_k_3(self) -> None:
        client = _FakeRetrievalClient()
        await run(_state_with([_make_subq("a")], tier="quick"), deps=_deps_with(client))
        assert client.search_calls[0].top_k == 3

    @pytest.mark.asyncio
    async def test_extreme_uses_top_k_10(self) -> None:
        client = _FakeRetrievalClient()
        await run(_state_with([_make_subq("a")], tier="extreme"), deps=_deps_with(client))
        assert client.search_calls[0].top_k == 10

    @pytest.mark.asyncio
    async def test_multiple_sub_questions_no_dependencies_run_in_parallel(self) -> None:
        client = _FakeRetrievalClient(
            search_hits={
                "Q1": [_make_hit(url="https://e.com/1")],
                "Q2": [_make_hit(url="https://e.com/2")],
                "Q3": [_make_hit(url="https://e.com/3")],
            }
        )
        subqs = [
            _make_subq("a", question="Q1"),
            _make_subq("b", question="Q2"),
            _make_subq("c", question="Q3"),
        ]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert len(patch["evidence"]) == 3
        assert {s["status"] for s in patch["sub_questions"]} == {"succeeded"}
        # 三次检索调用
        assert len(client.search_calls) == 3
        assert {c.query for c in client.search_calls} == {"Q1", "Q2", "Q3"}

    @pytest.mark.asyncio
    async def test_empty_search_results_mark_evidence_short(self) -> None:
        client = _FakeRetrievalClient(search_hits={})
        subqs = [_make_subq("a", question="没有结果")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        updated_subs = {s["id"]: s for s in patch["sub_questions"]}
        assert updated_subs["a"]["status"] == "evidence_short"
        assert updated_subs["a"]["evidence_ids"] == []
        assert patch["evidence"] == []

    @pytest.mark.asyncio
    async def test_extract_failure_returns_evidence_short(self) -> None:
        client = _FakeRetrievalClient(
            search_hits={"Q": [_make_hit(url="https://e.com/1")]},
            extract_raises={"https://e.com/1": RuntimeError("extract timeout")},
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        # 抽取失败被吃掉：search 已命中 1 条但 evidence 列表为空 → evidence_short
        updated_subs = {s["id"]: s for s in patch["sub_questions"]}
        assert updated_subs["a"]["status"] == "evidence_short"
        assert patch["evidence"] == []

    @pytest.mark.asyncio
    async def test_search_failure_isolated(self) -> None:
        client = _FakeRetrievalClient(
            search_hits={
                "good": [_make_hit(url="https://e.com/good")],
            },
            search_raises={"bad": RuntimeError("provider down")},
        )
        subqs = [
            _make_subq("a", question="bad"),
            _make_subq("b", question="good"),
        ]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        updated_subs = {s["id"]: s for s in patch["sub_questions"]}
        assert updated_subs["a"]["status"] == "failed"
        assert updated_subs["b"]["status"] == "succeeded"
        # 仅 good 产生证据
        assert len(patch["evidence"]) == 1
        assert patch["evidence"][0]["url"] == "https://e.com/good"

    @pytest.mark.asyncio
    async def test_topological_order_chain(self) -> None:
        """链式依赖 b -> a 必须等 a 检索完成后才开始。"""
        client = _FakeRetrievalClient(
            search_hits={
                "A": [_make_hit(url="https://e.com/a")],
                "B": [_make_hit(url="https://e.com/b")],
            }
        )
        a = _make_subq("a", question="A")
        b = _make_subq("b", question="B", depends_on=["a"])
        # 故意把 a 放后面传入，看拓扑算法是否能按依赖顺序分层
        patch = await run(_state_with([b, a]), deps=_deps_with(client))
        # 搜索顺序：a 先，b 后
        assert [c.query for c in client.search_calls] == ["A", "B"]

        # 子问题状态：两者都 succeeded
        statuses = {s["id"]: s["status"] for s in patch["sub_questions"]}
        assert statuses == {"a": "succeeded", "b": "succeeded"}

    @pytest.mark.asyncio
    async def test_skip_already_succeeded(self) -> None:
        """已 succeeded 的子问题不再重复检索。"""
        client = _FakeRetrievalClient()
        subqs = [
            _make_subq("a", question="A", status="succeeded"),
            _make_subq("b", question="B", status="pending"),
        ]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        # 仅 B 触发检索
        assert [c.query for c in client.search_calls] == ["B"]
        statuses = {s["id"]: s["status"] for s in patch["sub_questions"]}
        assert statuses["a"] == "succeeded"


# ---------------------------------------------------------------------------
# EvidenceDict 字段契约
# ---------------------------------------------------------------------------


class TestEvidenceFieldContract:
    @pytest.mark.asyncio
    async def test_strips_www_from_domain(self) -> None:
        client = _FakeRetrievalClient(
            search_hits={"Q": [_make_hit(url="https://www.example.com/x")]}
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert patch["evidence"][0]["domain"] == "example.com"

    @pytest.mark.asyncio
    async def test_title_truncated_to_512(self) -> None:
        long_title = "x" * 800
        client = _FakeRetrievalClient(
            search_hits={"Q": [_make_hit(url="https://e.com/1", title=long_title)]}
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert len(patch["evidence"][0]["title"]) == 512

    @pytest.mark.asyncio
    async def test_snippet_truncated_to_500(self) -> None:
        long_snippet = "s" * 800
        client = _FakeRetrievalClient(
            search_hits={"Q": [_make_hit(url="https://e.com/1", snippet=long_snippet)]}
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert len(patch["evidence"][0]["snippet"]) == 500

    @pytest.mark.asyncio
    async def test_published_at_propagated(self) -> None:
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        client = _FakeRetrievalClient(
            search_hits={"Q": [_make_hit(url="https://e.com/1", published_at=ts)]}
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert patch["evidence"][0]["published_at"] == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_intra_sub_question_dedup_by_fingerprint(self) -> None:
        # 两条 hit URL 完全相同（normalize 后）→ 仅保留一条
        client = _FakeRetrievalClient(
            search_hits={
                "Q": [
                    _make_hit(url="https://Example.com/x"),
                    _make_hit(url="https://example.com/x"),  # 规范化后同 URL
                ]
            }
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        assert len(patch["evidence"]) == 1

    @pytest.mark.asyncio
    async def test_evidence_id_is_ulid(self) -> None:
        client = _FakeRetrievalClient(
            search_hits={"Q": [_make_hit(url="https://e.com/1")]}
        )
        subqs = [_make_subq("a", question="Q")]
        patch = await run(_state_with(subqs), deps=_deps_with(client))
        ev_id = patch["evidence"][0]["id"]
        # ULID 26 字符（Crockford Base32）
        assert len(ev_id) == 26
        assert ev_id.isalnum()
        assert ev_id.isupper()
