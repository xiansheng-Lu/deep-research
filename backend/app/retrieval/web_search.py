"""Web 检索 Provider：M1 接入 Tavily。

业务层依赖 ``WebSearchProvider`` Protocol；具体实现由 ``TavilyProvider`` 提供。
"""
# pyright: reportGeneralTypeIssues=false

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tavily import AsyncTavilyClient

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalServiceError
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource

if TYPE_CHECKING:
    pass

log = logging.getLogger("retrieval.web_search")


@runtime_checkable
class WebSearchProvider(Protocol):
    """Web 检索 Provider 协议。

    所有具体实现（Tavily / SerpAPI / 自研抓取等）必须实现 ``search`` 与 ``extract``。
    """

    name: str

    async def aclose(self) -> None:
        """释放底层资源。"""
        ...

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        """执行一次 web 检索，返回 RetrievalHit 列表。"""
        ...

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        """对一组命中执行正文抽取，返回新的 RetrievalHit 列表（content 字段填充）。"""
        ...


class TavilyProvider:
    """Tavily 实现的 ``WebSearchProvider``。

    持有 ``AsyncTavilyClient``；search/extract 失败统一收敛为 ``ExternalServiceError``。
    """

    name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float = 60.0,
        client: AsyncTavilyClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TavilyProvider 需要非空的 api_key")
        self._timeout = timeout
        self._client = client or AsyncTavilyClient(api_key=api_key)

    async def aclose(self) -> None:
        """释放 httpx 会话。"""
        try:
            close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        except Exception as exc:  # noqa: BLE001 - 关闭时不做强错误处理
            log.warning("TavilyProvider aclose 异常: %s", exc)

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        """调用 Tavily ``search``，转换每条结果为 ``RetrievalHit``。"""
        if not request.query.strip():
            raise ValueError("RetrievalRequest.query 不能为空")
        kwargs: dict[str, Any] = {
            "max_results": max(1, request.top_k),
            "timeout": self._timeout,
        }
        if request.recency_days is not None and request.recency_days > 0:
            kwargs["days"] = request.recency_days
        try:
            payload = await self._client.search(request.query, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一收敛外部异常
            raise ExternalServiceError(
                f"Tavily search 失败：{exc}", details={"query": request.query}
            ) from exc
        return [_to_hit(item) for item in _iter_results(payload)]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        """对 ``hits`` 中带 URL 的条目批量抽取正文，返回新列表。"""
        targets: list[str] = []
        url_to_index: dict[str, int] = {}
        for idx, hit in enumerate(hits):
            if hit.url:
                targets.append(hit.url)
                url_to_index[hit.url] = idx
        if not targets:
            return list(hits)
        try:
            payload = await self._client.extract(
                urls=targets, timeout=self._timeout, format="markdown"
            )
        except Exception as exc:  # noqa: BLE001 - 统一收敛外部异常
            raise ExternalServiceError(
                f"Tavily extract 失败：{exc}", details={"count": len(targets)}
            ) from exc
        extracted_map = _index_extract_results(payload)
        result = list(hits)
        for url, content in extracted_map.items():
            idx = url_to_index.get(url)
            if idx is None:
                continue
            old = result[idx]
            result[idx] = RetrievalHit(
                source=old.source,
                title=old.title,
                url=old.url,
                snippet=old.snippet,
                score=old.score,
                content=content,
                published_at=old.published_at,
                fetched_at=old.fetched_at or datetime.now(tz=UTC),
                raw=old.raw,
            )
        return result


def _iter_results(payload: Any) -> list[dict[str, Any]]:
    """从 Tavily 响应中稳健地抽取 results 数组。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
    return []


def _to_hit(item: dict[str, Any]) -> RetrievalHit:
    """把单条 Tavily result 转成 RetrievalHit。"""
    url = item.get("url")
    title = str(item.get("title") or "")
    content = item.get("content") or item.get("raw_content") or item.get("snippet") or ""
    snippet = str(item.get("snippet") or content or "")[:500]
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    published_at = _parse_iso(item.get("published_date"))
    return RetrievalHit(
        source=RetrievalSource.WEB,
        title=title,
        url=url if isinstance(url, str) else None,
        snippet=snippet,
        content=str(content) if content else None,
        score=score,
        published_at=published_at,
        fetched_at=datetime.now(tz=UTC),
        raw={"provider": "tavily", "result": item},
    )


def _index_extract_results(payload: Any) -> dict[str, str]:
    """把 Tavily extract 响应按 URL 索引。"""
    out: dict[str, str] = {}
    items = (
        payload.get("results")
        if isinstance(payload, dict)
        else (payload if isinstance(payload, list) else [])
    )
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        raw_contents = item.get("raw_content")
        content = item.get("content")
        text = raw_contents or content or ""
        if isinstance(text, str) and text:
            out[url] = text
    return out


def _parse_iso(value: Any) -> datetime | None:
    """宽松解析 Tavily 返回的 published_date（ISO 字符串）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        # 兼容 "2024-09-10" 与 "2024-09-10T12:34:56Z"
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def build_tavily_provider(settings: Settings | None = None) -> TavilyProvider | None:
    """从全局配置构造 TavilyProvider；未配置 API key 时返回 None。"""
    cfg = settings or get_settings()
    raw = cfg.tavily_api_key.get_secret_value()
    if not raw:
        return None
    return TavilyProvider(api_key=raw)


def build_tavily_backup_provider(
    settings: Settings | None = None,
) -> TavilyProvider | None:
    """备份 Provider 暂等同于主 Provider（仅作为后续接入第二供应商的占位）。"""
    return build_tavily_provider(settings)
