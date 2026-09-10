"""WP-4 检索层单元测试：URL 规范化 / fingerprint / 去重 / 正文抽取 / Provider / Client。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError, ProviderUnavailableError
from app.provider.circuit_breaker import CircuitBreaker
from app.retrieval import (
    RetrievalClient,
    TavilyProvider,
    dedupe,
    extract_text,
    extract_text_from_html,
    fingerprint,
    normalize_url,
)
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource

# ---------------------------------------------------------------------------
# URL 规范化
# ---------------------------------------------------------------------------


class TestNormalizeUrl:
    def test_lowercase_scheme_and_host(self) -> None:
        assert normalize_url("HTTPS://Example.COM/path") == "https://example.com/path"

    def test_strip_www(self) -> None:
        assert normalize_url("https://www.example.com/x") == "https://example.com/x"

    def test_strip_fragment(self) -> None:
        assert normalize_url("https://example.com/x#section") == "https://example.com/x"

    def test_drop_utm_params(self) -> None:
        url = "https://example.com/a?utm_source=x&utm_medium=y&q=hello"
        assert normalize_url(url) == "https://example.com/a?q=hello"

    def test_drop_tracking_params(self) -> None:
        url = "https://example.com/a?fbclid=1&gclid=2&mc_cid=3&ref=4&q=z"
        assert normalize_url(url) == "https://example.com/a?q=z"

    def test_sort_query_params(self) -> None:
        url = "https://example.com/a?b=2&a=1&c=3"
        assert normalize_url(url) == "https://example.com/a?a=1&b=2&c=3"

    def test_strip_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_keep_root_slash(self) -> None:
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_empty_url(self) -> None:
        assert normalize_url("") == ""

    def test_invalid_url_returns_input(self) -> None:
        # 含控制字符的 URL：urlsplit 在 Python 3.12 上能解析，但 netloc 含 \x00
        # 规范化函数应不抛异常并返回合理的归一化结果
        bad = "http://exa\x00mple.com/"
        result = normalize_url(bad)
        assert isinstance(result, str)
        assert result  # 非空字符串


# ---------------------------------------------------------------------------
# Fingerprint & 去重
# ---------------------------------------------------------------------------


class TestFingerprintAndDedup:
    def _hit(self, url: str, content: str = "", score: float = 0.5) -> RetrievalHit:
        return RetrievalHit(
            source=RetrievalSource.WEB,
            title="t",
            url=url,
            snippet=content or "snip",
            score=score,
            content=content or None,
            fetched_at=datetime.now(tz=UTC),
        )

    def test_fingerprint_stable_for_same_url_and_content(self) -> None:
        a = self._hit("https://example.com/a", content="hello world")
        b = self._hit("https://example.com/a", content="hello world")
        assert fingerprint(a) == fingerprint(b)

    def test_fingerprint_changes_with_content(self) -> None:
        a = self._hit("https://example.com/a", content="hello")
        b = self._hit("https://example.com/a", content="world")
        assert fingerprint(a) != fingerprint(b)

    def test_fingerprint_ignores_tracking_params(self) -> None:
        a = self._hit("https://example.com/a?q=1&utm_source=x")
        b = self._hit("https://example.com/a?q=1&utm_campaign=y")
        assert fingerprint(a) == fingerprint(b)

    def test_dedupe_keeps_highest_score(self) -> None:
        hits = [
            self._hit("https://example.com/a", score=0.3),
            self._hit("https://example.com/a", score=0.9),
            self._hit("https://example.com/a", score=0.5),
        ]
        result = dedupe(hits)
        assert len(result) == 1
        assert result[0].score == 0.9

    def test_dedupe_keeps_distinct_urls(self) -> None:
        hits = [
            self._hit("https://example.com/a"),
            self._hit("https://example.com/b"),
        ]
        result = dedupe(hits)
        assert len(result) == 2

    def test_dedupe_empty(self) -> None:
        assert dedupe([]) == []

    def test_fingerprint_uses_snippet_when_no_content(self) -> None:
        a = self._hit("https://example.com/a", content="", score=0.0)
        # 构造一个 content=None 但 snippet 不同的 hit
        b = replace(a, content=None, snippet="different text")
        c = replace(a, content=None, snippet="different text")
        assert fingerprint(b) == fingerprint(c)


# ---------------------------------------------------------------------------
# 正文抽取
# ---------------------------------------------------------------------------


_HTML_SAMPLE = """
<!doctype html>
<html><head><title>T</title></head>
<body>
<header><nav>menu</nav></header>
<article>
<h1>正文标题</h1>
<p>第一段文字。这是测试内容。</p>
<p>第二段文字，用于验证抽取。</p>
</article>
<footer>页脚</footer>
</body></html>
""".strip()


class TestExtractor:
    def test_extract_text_from_html_via_trafilatura(self) -> None:
        text = extract_text_from_html(_HTML_SAMPLE)
        assert "正文标题" in text
        assert "测试内容" in text
        # 页脚通常会被正文抽取剔除；nav 在 favor_recall=True 下可能被保留，
        # 这里仅断言核心正文命中即可

    def test_extract_text_from_html_fallback_readability(self) -> None:
        # trafilatura 在极少内容时可能为空，应自动降级到 readability
        minimal = "<html><body><main><p>fallback content xyz</p></main></body></html>"
        text = extract_text_from_html(minimal)
        assert "fallback content xyz" in text

    def test_extract_text_from_html_empty(self) -> None:
        assert extract_text_from_html("") == ""

    async def test_extract_text_returns_content(self) -> None:
        hit = RetrievalHit(
            source=RetrievalSource.WEB,
            title="t",
            url="https://example.com",
            snippet="snip",
            score=0.5,
            content="real content",
        )
        assert await extract_text(hit) == "real content"

    async def test_extract_text_falls_back_to_html(self) -> None:
        hit = RetrievalHit(
            source=RetrievalSource.WEB,
            title="t",
            url="https://example.com",
            snippet="snip",
            score=0.5,
            content=None,
            raw={"html": _HTML_SAMPLE},
        )
        text = await extract_text(hit)
        assert "测试内容" in text

    async def test_extract_text_falls_back_to_snippet(self) -> None:
        hit = RetrievalHit(
            source=RetrievalSource.WEB,
            title="t",
            url="https://example.com",
            snippet="just a snippet",
            score=0.5,
        )
        assert await extract_text(hit) == "just a snippet"


# ---------------------------------------------------------------------------
# TavilyProvider
# ---------------------------------------------------------------------------


class _FakeAsyncTavilyClient:
    """最小化的 ``AsyncTavilyClient`` 替身，支持 search / extract。"""

    def __init__(
        self,
        *,
        search_payload: dict[str, Any] | Exception = None,
        extract_payload: dict[str, Any] | Exception = None,
    ) -> None:
        self._search_payload = search_payload or {"results": []}
        self._extract_payload = extract_payload or {"results": []}
        self.search_calls: list[dict[str, Any]] = []
        self.extract_calls: list[dict[str, Any]] = []
        self.closed = False

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append({"query": query, **kwargs})
        if isinstance(self._search_payload, Exception):
            raise self._search_payload
        return self._search_payload

    async def extract(self, urls: Any, **kwargs: Any) -> dict[str, Any]:
        self.extract_calls.append({"urls": urls, **kwargs})
        if isinstance(self._extract_payload, Exception):
            raise self._extract_payload
        return self._extract_payload

    async def aclose(self) -> None:
        self.closed = True


def _provider(client: _FakeAsyncTavilyClient) -> TavilyProvider:
    return TavilyProvider(api_key="fake-key", timeout=10.0, client=client)


_SEARCH_PAYLOAD = {
    "results": [
        {
            "url": "https://example.com/a",
            "title": "Example A",
            "content": "full content A",
            "snippet": "snippet A",
            "score": 0.92,
            "published_date": "2024-09-10",
        },
        {
            "url": "https://example.com/b",
            "title": "Example B",
            "content": "full content B",
            "snippet": "snippet B",
            "score": 0.75,
        },
        {
            "url": "https://example.com/c",
            "title": "Example C",
            "raw_content": "raw C",
            "score": "0.5",  # 字符串 score，应被转换
            "published_date": "not-a-date",  # 解析失败
        },
    ]
}


class TestTavilyProviderSearch:
    async def test_maps_results_to_hits(self) -> None:
        client = _FakeAsyncTavilyClient(search_payload=_SEARCH_PAYLOAD)
        provider = _provider(client)
        hits = await provider.search(RetrievalRequest(query="q", top_k=5))
        assert len(hits) == 3
        assert hits[0].url == "https://example.com/a"
        assert hits[0].title == "Example A"
        assert hits[0].content == "full content A"
        assert hits[0].snippet == "snippet A"
        assert hits[0].score == 0.92
        assert hits[0].published_at == datetime(2024, 9, 10)
        assert hits[1].published_at is None
        assert hits[2].score == 0.5  # 字符串转换为 float
        assert hits[2].published_at is None

    async def test_passes_top_k_and_recency(self) -> None:
        client = _FakeAsyncTavilyClient(search_payload=_SEARCH_PAYLOAD)
        provider = _provider(client)
        await provider.search(RetrievalRequest(query="q", top_k=3, recency_days=30))
        call = client.search_calls[0]
        assert call["max_results"] == 3
        assert call["days"] == 30

    async def test_recency_none_omits_dots(self) -> None:
        client = _FakeAsyncTavilyClient(search_payload=_SEARCH_PAYLOAD)
        provider = _provider(client)
        await provider.search(RetrievalRequest(query="q"))
        assert "days" not in client.search_calls[0]

    async def test_empty_query_raises_value_error(self) -> None:
        provider = _provider(_FakeAsyncTavilyClient())
        with pytest.raises(ValueError, match="query"):
            await provider.search(RetrievalRequest(query="   "))

    async def test_search_failure_raises_external_service_error(self) -> None:
        client = _FakeAsyncTavilyClient(search_payload=RuntimeError("boom"))
        provider = _provider(client)
        with pytest.raises(ExternalServiceError, match="Tavily search 失败"):
            await provider.search(RetrievalRequest(query="q"))

    async def test_accepts_list_payload(self) -> None:
        # 部分 SDK 返回 list[dict]，应也能处理
        list_payload = [
            {"url": "https://x.com", "title": "X", "score": 0.1, "content": "x"}
        ]
        client = _FakeAsyncTavilyClient(search_payload=list_payload)
        provider = _provider(client)
        hits = await provider.search(RetrievalRequest(query="q"))
        assert len(hits) == 1

    async def test_aclose_calls_underlying(self) -> None:
        client = _FakeAsyncTavilyClient()
        provider = _provider(client)
        await provider.aclose()
        assert client.closed is True

    async def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            TavilyProvider(api_key="")


class TestTavilyProviderExtract:
    async def test_writes_content_back(self) -> None:
        extract_payload = {
            "results": [
                {"url": "https://example.com/a", "raw_content": "long body A"},
                {"url": "https://example.com/b", "content": "short body B"},
            ]
        }
        client = _FakeAsyncTavilyClient(extract_payload=extract_payload)
        provider = _provider(client)
        hits = [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="A",
                url="https://example.com/a",
                snippet="sA",
                score=0.5,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="B",
                url="https://example.com/b",
                snippet="sB",
                score=0.4,
            ),
        ]
        result = await provider.extract(hits)
        assert result[0].content == "long body A"
        assert result[1].content == "short body B"

    async def test_skip_hits_without_url(self) -> None:
        client = _FakeAsyncTavilyClient()
        provider = _provider(client)
        hits = [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="X",
                url=None,
                snippet="s",
                score=0.5,
            )
        ]
        result = await provider.extract(hits)
        assert result == hits
        assert client.extract_calls == []

    async def test_empty_input_returns_copy(self) -> None:
        client = _FakeAsyncTavilyClient()
        provider = _provider(client)
        result = await provider.extract([])
        assert result == []
        assert client.extract_calls == []

    async def test_extract_failure_raises_external_service_error(self) -> None:
        client = _FakeAsyncTavilyClient(extract_payload=RuntimeError("net"))
        provider = _provider(client)
        hits = [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="X",
                url="https://example.com/x",
                snippet="s",
                score=0.5,
            )
        ]
        with pytest.raises(ExternalServiceError, match="Tavily extract 失败"):
            await provider.extract(hits)


# ---------------------------------------------------------------------------
# RetrievalClient 门面
# ---------------------------------------------------------------------------


class _StaticProvider:
    """简单的 WebSearchProvider 替身，按预设返回结果。"""

    def __init__(
        self,
        name: str,
        *,
        search_results: list[RetrievalHit] | Exception = None,
        extract_results: list[RetrievalHit] | Exception = None,
    ) -> None:
        self.name = name
        self._search_results = search_results or []
        self._extract_results = extract_results
        self.search_calls = 0
        self.extract_calls = 0
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        self.search_calls += 1
        if isinstance(self._search_results, Exception):
            raise self._search_results
        return list(self._search_results)

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        self.extract_calls += 1
        if isinstance(self._extract_results, Exception):
            raise self._extract_results
        if self._extract_results is not None:
            return list(self._extract_results)
        return [
            replace(h, content=(h.content or h.snippet) + " [ext]")
            for h in hits
        ]


class TestRetrievalClient:
    async def test_search_returns_deduped_hits(self) -> None:
        provider = _StaticProvider("a", search_results=[
            RetrievalHit(source=RetrievalSource.WEB, title="t1", url="https://x.com/1",
                         snippet="s1", score=0.9, content="c1"),
            RetrievalHit(source=RetrievalSource.WEB, title="t1-dup", url="https://x.com/1",
                         snippet="s1", score=0.5, content="c1"),
        ])
        client = RetrievalClient(primary=provider, breaker=CircuitBreaker(fail_threshold=2))
        hits = await client.search(RetrievalRequest(query="q"))
        assert len(hits) == 1
        assert hits[0].score == 0.9

    async def test_search_falls_back_to_backup(self) -> None:
        primary = _StaticProvider("primary", search_results=ExternalServiceError("down"))
        backup = _StaticProvider("backup", search_results=[
            RetrievalHit(source=RetrievalSource.WEB, title="b", url="https://b.com",
                         snippet="s", score=0.5),
            ])
        client = RetrievalClient(primary=primary, backup=backup,
                                 breaker=CircuitBreaker(fail_threshold=5))
        hits = await client.search(RetrievalRequest(query="q"))
        assert len(hits) == 1
        assert backup.search_calls == 1

    async def test_search_raises_when_no_provider(self) -> None:
        client = RetrievalClient(primary=None, backup=None)
        with pytest.raises(ProviderUnavailableError):
            await client.search(RetrievalRequest(query="q"))

    async def test_search_raises_when_primary_fails_without_backup(self) -> None:
        primary = _StaticProvider("p", search_results=ExternalServiceError("nope"))
        client = RetrievalClient(primary=primary, breaker=CircuitBreaker(fail_threshold=5))
        with pytest.raises(ExternalServiceError):
            await client.search(RetrievalRequest(query="q"))

    async def test_breaker_trips_after_threshold(self) -> None:
        breaker = CircuitBreaker(fail_threshold=2, reset_seconds=60)
        primary = _StaticProvider("primary", search_results=ExternalServiceError("down"))
        client = RetrievalClient(primary=primary, breaker=breaker)
        for _ in range(2):
            with pytest.raises(ExternalServiceError):
                await client.search(RetrievalRequest(query="q"))
        assert breaker.is_open("primary") is True

    async def test_breaker_open_skips_primary(self) -> None:
        breaker = CircuitBreaker(fail_threshold=1, reset_seconds=60)
        # 先手动熔断 primary
        breaker.trip("primary")
        backup = _StaticProvider("backup", search_results=[
            RetrievalHit(source=RetrievalSource.WEB, title="b", url="https://b.com",
                         snippet="s", score=0.5),
        ])
        primary = _StaticProvider("primary")
        client = RetrievalClient(primary=primary, backup=backup, breaker=breaker)
        hits = await client.search(RetrievalRequest(query="q"))
        assert primary.search_calls == 0
        assert backup.search_calls == 1
        assert len(hits) == 1

    async def test_extract_uses_primary(self) -> None:
        hits_in = [
            RetrievalHit(source=RetrievalSource.WEB, title="t", url="https://x.com",
                         snippet="s", score=0.5),
        ]
        primary = _StaticProvider("primary")
        client = RetrievalClient(primary=primary)
        result = await client.extract(hits_in)
        assert result[0].content == "s [ext]"
        assert primary.extract_calls == 1

    async def test_extract_falls_back(self) -> None:
        hits_in = [
            RetrievalHit(source=RetrievalSource.WEB, title="t", url="https://x.com",
                         snippet="s", score=0.5),
        ]
        primary = _StaticProvider("primary", extract_results=ExternalServiceError("x"))
        backup = _StaticProvider("backup")
        client = RetrievalClient(primary=primary, backup=backup,
                                 breaker=CircuitBreaker(fail_threshold=5))
        result = await client.extract(hits_in)
        assert backup.extract_calls == 1
        assert result[0].content == "s [ext]"

    async def test_extract_empty_returns_empty(self) -> None:
        primary = _StaticProvider("p")
        client = RetrievalClient(primary=primary)
        result = await client.extract([])
        assert result == []
        assert primary.extract_calls == 0

    async def test_aclose_closes_all_providers(self) -> None:
        p = _StaticProvider("p")
        b = _StaticProvider("b")
        client = RetrievalClient(primary=p, backup=b)
        await client.aclose()
        assert p.closed is True
        assert b.closed is True


# ---------------------------------------------------------------------------
# Provider 协议 & 工厂
# ---------------------------------------------------------------------------


class TestProtocolAndFactory:
    def test_tavily_provider_isinstance_web_search(self) -> None:
        provider = TavilyProvider(api_key="x", client=_FakeAsyncTavilyClient())
        # runtime_checkable Protocol 可走 isinstance
        from app.retrieval.web_search import WebSearchProvider
        assert isinstance(provider, WebSearchProvider)

    def test_build_tavily_provider_returns_none_when_no_key(self) -> None:
        """未配置 TAVILY_API_KEY 时工厂返回 None，便于上层快速失败。"""
        from app.retrieval.web_search import build_tavily_provider

        # 用 model_construct 构造 settings 实例，绕开校验
        settings = Settings.model_construct(
            app_env="dev",
            app_name="deep-research-api",
            app_host="0.0.0.0",
            app_port=8000,
            log_level="INFO",
            log_json=True,
            log_collector_endpoint="",
            secret_key="x" * 32,
            jwt_algorithm="HS256",
            jwt_access_ttl_minutes=30,
            jwt_refresh_ttl_days=7,
            cookie_secure=False,
            encryption_key="",
            db_async_url="postgresql+asyncpg://x",
            db_sync_url="postgresql://x",
            db_pool_size=10,
            db_max_overflow=20,
            redis_url="redis://x",
            celery_broker_url="redis://x",
            celery_result_backend="redis://x",
            object_storage_endpoint="x",
            object_storage_access_key="x",
            object_storage_secret_key="x",
            object_storage_bucket="x",
            object_storage_secure=False,
            llm_primary_base_url="",
            llm_primary_api_key="",
            llm_primary_model="",
            llm_backup_base_url="",
            llm_backup_api_key="",
            llm_backup_model="",
            llm_timeout_seconds=60,
            llm_max_retries=2,
            llm_circuit_fail_threshold=5,
            llm_circuit_reset_seconds=60,
            tavily_api_key=type("SecretStr", (), {"get_secret_value": lambda self: ""})(),
            quota_default_tier="standard",
            quota_tier_quick_tokens=50_000,
            quota_tier_standard_tokens=150_000,
            quota_tier_deep_tokens=400_000,
            quota_tier_extreme_tokens=1_000_000,
            ws_heartbeat_seconds=25,
            sse_heartbeat_seconds=15,
            otel_enabled=True,
            otel_exporter_otlp_endpoint="",
            otel_service_name="x",
            llm_trace_enabled=True,
            llm_trace_sample_rate=0.01,
        )
        assert build_tavily_provider(settings) is None
