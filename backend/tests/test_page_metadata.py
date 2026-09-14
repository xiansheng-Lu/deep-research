"""M2-6 T3 页面元数据补采单测（AC-7）。

网络层用 httpx MockTransport 离线模拟，不发真实请求；开关/配额/超时降级
通过 settings 注入覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient as _RealAsyncClient

from app.retrieval.page_metadata import (
    extract_metadata_from_html,
    fetch_page_metadata,
    parse_page_date,
)


class TestParsePageDate:
    def test_iso8601_with_z(self) -> None:
        dt = parse_page_date("2026-09-10T08:57:00Z")
        assert dt == datetime(2026, 9, 10, 8, 57, tzinfo=UTC)

    def test_iso8601_date_only(self) -> None:
        dt = parse_page_date("2026-09-10")
        assert dt is not None and dt.year == 2026 and dt.month == 9

    def test_rfc2822(self) -> None:
        dt = parse_page_date("Fri, 10 Sep 2026 08:57:00 GMT")
        assert dt == datetime(2026, 9, 10, 8, 57, tzinfo=UTC)

    @pytest.mark.parametrize("bad", [None, "", "not a date", "昨天", 123])
    def test_invalid_returns_none(self, bad: object) -> None:
        assert parse_page_date(bad) is None  # type: ignore[arg-type]


_BODY = "<article>正文内容用于通过抽取阈值。" + "填充文本" * 300 + "</article>"


class TestExtractFromHtml:
    def test_extracts_date_and_sitename(self) -> None:
        html = (
            '<html><head><meta property="article:published_time" '
            'content="2026-05-01T10:00:00Z"><meta property="og:site_name" '
            'content="国家统计局"><title>统计公报</title></head><body>' + _BODY + "</body></html>"
        )
        meta = extract_metadata_from_html(html, url="https://stats.gov.cn/x")
        # trafilatura 把 ISO 日期归一化为 YYYY-MM-DD
        assert meta.published_at is not None
        assert (meta.published_at.year, meta.published_at.month, meta.published_at.day) == (
            2026,
            5,
            1,
        )
        assert meta.site_name == "国家统计局"

    def test_empty_html_returns_empty_metadata(self) -> None:
        meta = extract_metadata_from_html("")
        assert meta.published_at is None and meta.site_name is None and meta.author is None

    def test_unparseable_date_is_none_but_keeps_sitename(self) -> None:
        html = (
            '<html><head><meta property="article:published_time" content="很久以前">'
            '<meta property="og:site_name" content="某站"></head><body>' + _BODY + "</body></html>"
        )
        meta = extract_metadata_from_html(html)
        assert meta.published_at is None
        assert meta.site_name == "某站"


def _settings(*, enabled: bool = True, per_subq: int = 3, timeout: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(
        source_page_metadata_enabled=enabled,
        source_page_fetch_per_subquestion=per_subq,
        source_page_fetch_timeout_seconds=timeout,
    )


def _client_with(handler: object) -> httpx.AsyncClient:
    # 用模块导入时绑定的真实类，避免测试 monkeypatch httpx.AsyncClient 后递归
    return _RealAsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


class TestFetchPageMetadata:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        async def _handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, text="<html></html>")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_with(_handler))
        result = await fetch_page_metadata(["https://example.com/a"], settings=_settings(enabled=False))
        assert result == {} and called["n"] == 0

    @pytest.mark.asyncio
    async def test_fetches_only_missing_dates_and_respects_quota(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            '<html><head><meta property="article:published_time" content="2026-05-01T00:00:00Z">'
            '<meta property="og:site_name" content="站点"></head><body>' + _BODY + "</body></html>"
        )

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_with(_handler))
        urls = [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
            "https://example.com/d",
        ]
        result = await fetch_page_metadata(urls, settings=_settings(per_subq=2))
        assert len(result) == 2  # 配额截断
        for meta in result.values():
            assert meta.published_at == datetime(2026, 5, 1, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_network_error_silently_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_with(_handler))
        result = await fetch_page_metadata(["https://example.com/a"], settings=_settings())
        assert result == {}  # 静默降级，不抛异常

    @pytest.mark.asyncio
    async def test_non_http_and_duplicates_filtered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text="<html></html>")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_with(_handler))
        result = await fetch_page_metadata(
            [
                "ftp://example.com/file",
                None,  # type: ignore[list-item]
                "https://example.com/a",
                "https://example.com/a",  # 去重
            ],
            settings=_settings(),
        )
        assert seen == ["https://example.com/a"]
        # 无日期元数据 → 不入结果
        assert result == {}
