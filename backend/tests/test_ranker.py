"""M2-6 T2 相关性打分器单测（AC-3/AC-4）。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.retrieval.base import RetrievalHit, RetrievalSource
from app.retrieval.ranker import blend_score, rerank, score_hit, score_relevance


def _hit(title: str, snippet: str, *, score: float = 0.0, content: str | None = None) -> RetrievalHit:
    return RetrievalHit(
        source=RetrievalSource.WEB,
        title=title,
        url="https://example.com/x",
        snippet=snippet,
        score=score,
        content=content,
        fetched_at=datetime.now(tz=UTC),
    )


class TestScoreRelevance:
    def test_relevant_evidence_scores_higher_than_irrelevant(self) -> None:
        query = "新能源汽车销量"
        hit = score_relevance(
            query,
            title="2026 年新能源汽车销量创新高",
            snippet="全年新能源汽车销量达到 1200 万辆，同比增长四成",
        )
        miss = score_relevance(
            query,
            title="今日天气晴朗",
            snippet="全国大部分地区气温回升，适合户外活动与旅行",
        )
        assert hit > miss
        assert hit > 0.0

    def test_score_in_unit_interval_and_three_decimals(self) -> None:
        score = score_relevance(
            "新能源汽车销量",
            title="新能源汽车",
            snippet="销量增长",
            content="正文" * 500,
        )
        assert 0.0 <= score <= 1.0
        assert round(score, 3) == score

    def test_empty_or_stopword_only_query_returns_zero(self) -> None:
        # 全是停用字/无有效词项 → 0.0
        assert score_relevance("什么的问题", title="任意", snippet="文本") == 0.0
        assert score_relevance("", title="任意", snippet="文本") == 0.0

    def test_title_match_contributes(self) -> None:
        query = "光伏装机"
        with_title = score_relevance(query, title="光伏装机规模", snippet="不相关正文")
        body_only = score_relevance(query, title="行业观察", snippet="光伏装机规模数据")
        # 标题命中的权重贡献应使其不低于仅正文命中
        assert with_title > 0.0
        assert body_only > 0.0
        assert with_title >= body_only

    def test_content_over_limit_truncated(self) -> None:
        # 超长正文只取前 2000 字符：命中词在截断区外、标题/摘要也不含时为 0
        query = "尾部"
        long_irrelevant = "普通内容" * 2000  # 远超 2000 字符，且不含「尾部」
        assert "尾部" not in long_irrelevant[:2000]
        score = score_relevance(query, title="无", snippet="无", content=long_irrelevant + "尾部关键词")
        assert score == 0.0


class TestBlendScore:
    def test_provider_zero_uses_lexical(self) -> None:
        # 博查形态：provider 恒 0，直接取词面分（修复真链恒 0）
        assert blend_score(0.0, 0.62) == 0.62
        assert blend_score(None, 0.62) == 0.62

    def test_provider_score_blends_half(self) -> None:
        # 0.5/0.5 融合
        assert blend_score(0.8, 0.4) == round(0.5 * 0.8 + 0.5 * 0.4, 3)

    def test_blend_clamped_to_one(self) -> None:
        assert blend_score(1.0, 1.0) == 1.0
        assert blend_score(2.0, 2.0) == 1.0


class TestRerank:
    def test_rerank_by_blended_desc(self) -> None:
        query = "新能源汽车"
        high = _hit("新能源汽车销量报告", "新能源汽车销量大幅增长", score=0.9)
        low = _hit("无关天气", "晴朗", score=0.1)
        result = rerank([low, high], query)
        assert result == [high, low]

    def test_rerank_empty(self) -> None:
        assert rerank([], "q") == []

    def test_rerank_without_query_uses_provider_score(self) -> None:
        high = _hit("a", "a", score=0.9)
        low = _hit("b", "b", score=0.1)
        assert rerank([low, high], None) == [high, low]

    def test_score_hit_nonzero_in_bocha_shape(self) -> None:
        # AC-4：无 provider 分但词面命中时，融合分非零
        hit = _hit("新能源汽车", "新能源汽车产业观察", score=0.0)
        assert score_hit("新能源汽车", hit) > 0.0
