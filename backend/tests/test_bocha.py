"""博查 AI 搜索 Provider 单元测试（不连真实网络，mock httpx 响应）。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError
from app.retrieval.base import RetrievalRequest, RetrievalSource
from app.retrieval.bocha import (
    DEFAULT_BASE_URL,
    BochaProvider,
    _iter_results,
    _parse_published_at,
    _to_freshness,
    _to_hit,
    build_bocha_provider,
)
from app.retrieval.client import RetrievalClient


def _settings(**overrides: Any) -> Settings:
    """用 model_construct 绕开必填项校验，仅填充检索相关字段。"""
    base: dict[str, Any] = {
        "web_search_provider": "bocha",
        "bocha_api_key": SecretStr("test-bocha-key"),
        "bocha_base_url": DEFAULT_BASE_URL,
        "bocha_timeout_seconds": 30.0,
        "tavily_api_key": SecretStr(""),
    }
    base.update(overrides)
    return Settings.model_construct(**base)


def _payload() -> dict[str, Any]:
    """博查标准响应样例（open.bochaai.com 文档形态）。"""
    return {
        "code": 200,
        "log_id": "log-1",
        "msg": None,
        "data": {
            "_type": "SearchResponse",
            "queryContext": {"originalQuery": "deepseek"},
            "webPages": {
                "webSearchUrl": "https://bochaai.com/search?q=deepseek",
                "totalEstimatedMatches": 1,
                "value": [
                    {
                        "name": "DeepSeek 官网",
                        "url": "https://www.deepseek.com/",
                        "displayUrl": "https://www.deepseek.com/",
                        "snippet": "DeepSeek 是一家人工智能公司",
                        "summary": "DeepSeek 致力于实现通用人工智能，发布了开源大模型。",
                        "siteName": "deepseek",
                        "datePublished": "2026-09-10T08:57:00Z",
                        "language": "zh",
                        "isFamilyFriendly": True,
                    }
                ],
            },
        },
    }


# ---------------------------------------------------------------------------
# 入参映射
# ---------------------------------------------------------------------------


class TestToFreshness:
    def test_none_or_zero_is_no_limit(self) -> None:
        assert _to_freshness(None) == "noLimit"
        assert _to_freshness(0) == "noLimit"

    def test_breakpoints(self) -> None:
        assert _to_freshness(1) == "oneDay"
        assert _to_freshness(7) == "oneWeek"
        assert _to_freshness(31) == "oneMonth"
        assert _to_freshness(365) == "oneYear"
        assert _to_freshness(400) == "noLimit"


# ---------------------------------------------------------------------------
# 时间解析
# ---------------------------------------------------------------------------


class TestParsePublishedAt:
    def test_iso8601(self) -> None:
        parsed = _parse_published_at("2026-09-10T08:57:00Z")
        assert parsed is not None
        assert parsed.year == 2026 and parsed.month == 9 and parsed.day == 10

    def test_rfc2822(self) -> None:
        parsed = _parse_published_at("Thu, 10 Sep 2026 08:57:00 GMT")
        assert parsed is not None
        assert parsed.year == 2026

    def test_invalid(self) -> None:
        assert _parse_published_at("not a date") is None
        assert _parse_published_at(None) is None
        assert _parse_published_at("") is None


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------


class TestIterResults:
    def test_standard_envelope(self) -> None:
        items = _iter_results(_payload())
        assert len(items) == 1
        assert items[0]["name"] == "DeepSeek 官网"

    def test_direct_envelope(self) -> None:
        """兼容直连搜索响应（无 data 包裹层）。"""
        payload = {"webPages": {"value": [{"name": "x"}]}}
        assert _iter_results(payload)[0]["name"] == "x"

    def test_empty_or_bad(self) -> None:
        assert _iter_results({}) == []
        assert _iter_results({"data": {}}) == []
        assert _iter_results({"data": {"webPages": {}}}) == []
        assert _iter_results("bad") == []


class TestToHit:
    def test_maps_fields(self) -> None:
        item = _iter_results(_payload())[0]
        hit = _to_hit(item)
        assert hit.source == RetrievalSource.WEB
        assert hit.title == "DeepSeek 官网"
        assert hit.url == "https://www.deepseek.com/"
        assert "人工智能" in hit.snippet
        # summary 写入 content，score 博查不提供
        assert hit.content is not None and "开源大模型" in hit.content
        assert hit.score == 0.0
        assert hit.published_at is not None
        assert hit.raw["provider"] == "bocha"


# ---------------------------------------------------------------------------
# Provider search / extract
# ---------------------------------------------------------------------------


class TestBochaProviderSearch:
    def _response(self, status: int, payload: dict[str, Any]) -> httpx.Response:
        """构造绑定了 request 的 Response（raise_for_status 需要 request 实例）。"""
        request = httpx.Request(
            "POST", f"{DEFAULT_BASE_URL}/web-search"
        )
        return httpx.Response(status, json=payload, request=request)

    def _provider(self, response: httpx.Response | None = None) -> BochaProvider:
        provider = BochaProvider(api_key="k")
        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            return_value=response or self._response(200, _payload())
        )
        provider._client = mock_client
        return provider

    async def test_search_success(self) -> None:
        provider = self._provider()
        hits = await provider.search(RetrievalRequest(query="deepseek", top_k=5))
        assert len(hits) == 1
        assert hits[0].title == "DeepSeek 官网"
        # 校验请求体：summary 必须开启（content 数据来源）
        _, kwargs = provider._client.post.call_args
        assert kwargs["json"]["summary"] is True
        assert kwargs["json"]["count"] == 5
        assert kwargs["json"]["freshness"] == "noLimit"
        await provider.aclose()

    async def test_search_business_error(self) -> None:
        response = self._response(200, {"code": 401, "msg": "invalid api key"})
        provider = self._provider(response)
        with pytest.raises(ExternalServiceError, match="业务错误码"):
            await provider.search(RetrievalRequest(query="x"))

    async def test_search_http_error(self) -> None:
        def _raise(*args: Any, **kwargs: Any) -> httpx.Response:
            raise httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock()
            )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=_raise)
        provider = BochaProvider(api_key="k")
        provider._client = mock_client
        with pytest.raises(ExternalServiceError, match="博查 search 失败"):
            await provider.search(RetrievalRequest(query="x"))

    async def test_empty_query_raises(self) -> None:
        provider = self._provider()
        with pytest.raises(ValueError):
            await provider.search(RetrievalRequest(query="  "))

    async def test_extract_passes_through(self) -> None:
        """博查无抽取端点：extract 直接返回命中列表（content 已由 summary 填充）。"""
        provider = self._provider()
        hits = await provider.search(RetrievalRequest(query="deepseek"))
        extracted = await provider.extract(hits)
        assert extracted is not hits  # 浅拷贝，非同一列表
        assert extracted == hits
        assert all(h.content for h in extracted)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


class TestBuildBochaProvider:
    def test_no_key_returns_none(self) -> None:
        settings = _settings(bocha_api_key=SecretStr(""))
        assert build_bocha_provider(settings) is None

    def test_with_key_builds(self) -> None:
        provider = build_bocha_provider(_settings())
        assert isinstance(provider, BochaProvider)
        assert provider.name == "bocha"

    def test_empty_key_constructor_raises(self) -> None:
        with pytest.raises(ValueError):
            BochaProvider(api_key="")


# ---------------------------------------------------------------------------
# RetrievalClient.from_settings 供应方选择
# ---------------------------------------------------------------------------


class TestRetrievalClientFromSettings:
    def test_bocha_selected(self) -> None:
        client = RetrievalClient.from_settings(_settings())
        assert client is not None
        assert client._primary.name == "bocha"  # type: ignore[union-attr]

    def test_bocha_without_key_returns_none(self) -> None:
        settings = _settings(bocha_api_key=SecretStr(""))
        assert RetrievalClient.from_settings(settings) is None

    def test_tavily_selected_when_configured(self) -> None:
        settings = _settings(
            web_search_provider="tavily",
            tavily_api_key=SecretStr("tvly-test"),
        )
        client = RetrievalClient.from_settings(settings)
        assert client is not None
        assert client._primary.name == "tavily"  # type: ignore[union-attr]

    def test_unknown_provider_returns_none(self) -> None:
        settings = _settings(web_search_provider="unknown")  # type: ignore[arg-type]
        assert RetrievalClient.from_settings(settings) is None
