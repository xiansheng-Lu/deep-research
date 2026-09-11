"""Web 检索 Provider：博查 AI 搜索（国内公域检索，https://open.bochaai.com）。

实现 ``app.retrieval.web_search.WebSearchProvider`` 协议，对接博查开放平台
Web Search API（``POST /v1/web-search``，Bearer 鉴权）。

与 Tavily 适配的差异：
- 博查无独立正文抽取端点：``search`` 时传 ``summary=true`` 即返回长摘要，
  ``extract`` 直接透传已填充摘要的命中（不产生二次网络请求）。
- 博查不返回相关性分数，``RetrievalHit.score`` 统一置 0.0，可信度由
  standardizer 节点按来源等级启发式判定。
"""
# pyright: reportGeneralTypeIssues=false

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalServiceError
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource

log = logging.getLogger("retrieval.bocha")

# 博查开放平台默认服务地址
DEFAULT_BASE_URL = "https://api.bochaai.com/v1"

# recency_days → 博查 freshness 枚举的分界（天）
# 博查仅支持 oneDay / oneWeek / oneMonth / oneYear / noLimit 五档
_FRESHNESS_BREAKPOINTS: tuple[tuple[int, str], ...] = (
    (1, "oneDay"),
    (7, "oneWeek"),
    (31, "oneMonth"),
    (365, "oneYear"),
)


class BochaProvider:
    """博查 AI 搜索实现的 ``WebSearchProvider``。

    持有 ``httpx.AsyncClient``；search/extract 失败统一收敛为 ``ExternalServiceError``。
    """

    name = "bocha"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BochaProvider 需要非空的 api_key")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def aclose(self) -> None:
        """释放 httpx 会话。"""
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001 - 关闭时不做强错误处理
            log.warning("BochaProvider aclose 异常: %s", exc)

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        """调用博查 ``/web-search``，转换每条结果为 ``RetrievalHit``。"""
        if not request.query.strip():
            raise ValueError("RetrievalRequest.query 不能为空")
        body: dict[str, Any] = {
            "query": request.query,
            "summary": True,
            "count": max(1, request.top_k),
            "freshness": _to_freshness(request.recency_days),
        }
        try:
            resp = await self._client.post("/web-search", json=body)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - 统一收敛外部异常
            raise ExternalServiceError(
                f"博查 search 失败：{exc}", details={"query": request.query}
            ) from exc

        # 业务层错误码（HTTP 200 但 code 非 200）
        code = payload.get("code") if isinstance(payload, dict) else None
        if code is not None and code != 200:
            raise ExternalServiceError(
                f"博查 search 返回业务错误码 {code}：{payload.get('msg')}",
                details={"query": request.query},
            )
        return [_to_hit(item) for item in _iter_results(payload)]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        """博查无独立正文抽取端点。

        ``search(summary=true)`` 返回的 ``summary`` 已在检索阶段写入 ``content``，
        此处直接返回命中列表的浅拷贝，保持 WebSearchProvider 协议一致。
        """
        return list(hits)


def _to_freshness(recency_days: int | None) -> str:
    """把时间范围天数映射为博查 freshness 枚举；None 表示不限制。"""
    if recency_days is None or recency_days <= 0:
        return "noLimit"
    for days, freshness in _FRESHNESS_BREAKPOINTS:
        if recency_days <= days:
            return freshness
    return "noLimit"


def _iter_results(payload: Any) -> list[dict[str, Any]]:
    """从博查响应中抽取 ``data.webPages.value`` 数组。

    兼容两种外层形态：
    - 标准开放平台响应：``{"code":200,"data":{"webPages":{"value":[...]}}}``
    - 直连搜索响应：``{"webPages":{"value":[...]}}``
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    web_pages = data.get("webPages")
    if not isinstance(web_pages, dict):
        return []
    value = web_pages.get("value")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _to_hit(item: dict[str, Any]) -> RetrievalHit:
    """把单条博查 webPages.value 条目转成 RetrievalHit。"""
    url = item.get("url") or item.get("displayUrl")
    title = str(item.get("name") or "")
    summary = item.get("summary") or ""
    snippet = str(item.get("snippet") or summary or "")[:500]
    content = str(summary) if summary else None
    return RetrievalHit(
        source=RetrievalSource.WEB,
        title=title,
        url=url if isinstance(url, str) else None,
        snippet=snippet,
        score=0.0,
        content=content,
        published_at=_parse_published_at(item.get("datePublished")),
        fetched_at=datetime.now(tz=UTC),
        raw={"provider": "bocha", "result": item},
    )


def _parse_published_at(value: Any) -> datetime | None:
    """宽松解析博查返回的发布时间（ISO 8601 或 RFC 2822 英文日期）。"""
    if not isinstance(value, str) or not value:
        return None
    cleaned = value.strip()
    # 优先按 ISO 8601 解析（如 2026-09-10T08:57:00Z）
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        pass
    # 再按 RFC 2822 / 英文日期解析（如 Fri, 10 Sep 2026 08:57:00 GMT）
    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError):
        return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def build_bocha_provider(settings: Settings | None = None) -> BochaProvider | None:
    """从全局配置构造 BochaProvider；未配置 API key 时返回 None。"""
    cfg = settings or get_settings()
    raw = cfg.bocha_api_key.get_secret_value()
    if not raw:
        return None
    return BochaProvider(
        api_key=raw,
        base_url=cfg.bocha_base_url or DEFAULT_BASE_URL,
        timeout=cfg.bocha_timeout_seconds,
    )


__all__ = ["BochaProvider", "build_bocha_provider", "DEFAULT_BASE_URL"]
