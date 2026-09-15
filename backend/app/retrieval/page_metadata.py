"""页面级信源元数据补采（M2-6 T3）。

Provider（尤其博查）不返回发布时间时，按小预算抓取页面 HTML，经 trafilatura
``bare_extraction(with_metadata=True)`` 补采 ``date`` / ``sitename`` / ``author``。

硬约束（方案 §5.5）：

- 仅补采无 provider 日期的命中，每子问题配额可配（默认 3 条）；
- 单页超时、信号量限并发；网络/反爬/JS 渲染失败一律静默降级，不影响
  证据入库与子问题成功状态；
- 不做 robots/Cookie/代理/重试；只发常规 GET，不带用户凭据；
- 补采的 sitename 只进证据 metadata_，不反解域名分类。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
import trafilatura

from app.core.config import Settings, get_settings

log = logging.getLogger("retrieval.page_metadata")

# 常规浏览器 UA：部分站点对无 UA 请求直接拒绝/返回反爬页
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """单页补采结果；任一字段缺失为 None。"""

    published_at: datetime | None
    site_name: str | None
    author: str | None


def parse_page_date(value: Any) -> datetime | None:
    """宽松解析页面元数据中的发布时间（ISO 8601 / RFC 2822 / YYYY-MM-DD）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    # ISO 8601（含末尾 Z）
    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    # RFC 2822 英文日期
    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def extract_metadata_from_html(html: str, *, url: str | None = None) -> PageMetadata:
    """从已抓取 HTML 解析页面元数据（纯函数，便于离线单测）。"""
    if not html:
        return PageMetadata(None, None, None)
    try:
        data = trafilatura.bare_extraction(
            html,
            url=url,
            with_metadata=True,
            favor_recall=True,
            include_comments=False,
            include_tables=False,
            as_dict=True,
        )
    except Exception as exc:  # noqa: BLE001 - 解析失败降级为空元数据
        log.debug("trafilatura 元数据解析失败: %s", exc)
        return PageMetadata(None, None, None)
    if not isinstance(data, dict):
        return PageMetadata(None, None, None)

    site_name = data.get("sitename") or data.get("hostname")
    author = data.get("author")
    return PageMetadata(
        published_at=parse_page_date(data.get("date")),
        site_name=str(site_name).strip() or None,
        author=str(author).strip() or None,
    )


def _is_supported_url(url: str | None) -> bool:
    """仅对 http/https 公网页面发起补采。"""
    if not url:
        return False
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


async def _fetch_one(client: httpx.AsyncClient, url: str) -> PageMetadata | None:
    """抓取并解析单页；任何异常返回 None（静默降级）。

    单页超时由 ``AsyncClient(timeout=...)`` 统一承载（不在 async 函数签名
    暴露 timeout 形参，与 fan-out 用 wait_for 外包的代码库口径一致）。
    """
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:  # noqa: BLE001 - 网络/超时/状态码异常均降级
        log.debug("页面元数据补采抓取失败 %s: %s", url, exc)
        return None
    return extract_metadata_from_html(html, url=url)


async def fetch_page_metadata(
    urls: list[str],
    *,
    settings: Settings | None = None,
    limit: int | None = None,
) -> dict[str, PageMetadata]:
    """对一批 URL 受控并发补采页面元数据，返回 url → 结果（仅成功项）。

    - 开关关闭 / 配额为 0 / 入参为空 → 返回空字典；
    - 仅 http(s) URL、去重后按原顺序取前 ``limit`` 条；
    - 信号量把并发限制在配额以内（默认 ≤3），避免瞬时打爆目标站。
    """
    cfg = settings or get_settings()
    if not cfg.source_page_metadata_enabled:
        return {}
    quota = int(limit if limit is not None else cfg.source_page_fetch_per_subquestion)
    if quota <= 0 or not urls:
        return {}

    # 去重 + 协议过滤 + 截断配额（保持调用方给定顺序）
    targets: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not _is_supported_url(url) or url in seen:
            continue
        seen.add(url)
        targets.append(url)
        if len(targets) >= quota:
            break
    if not targets:
        return {}

    timeout = float(cfg.source_page_fetch_timeout_seconds)
    semaphore = asyncio.Semaphore(min(quota, len(targets)))
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    results: dict[str, PageMetadata] = {}
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:

        async def _guarded(url: str) -> tuple[str, PageMetadata | None]:
            async with semaphore:
                return url, await _fetch_one(client, url)

        pairs = await asyncio.gather(*(_guarded(url) for url in targets))
    for url, meta in pairs:
        # 仅在抓到任一有效元数据时入表；空页/反爬页的空结果视为未补采到
        if meta is not None and (
            meta.published_at is not None or meta.site_name is not None or meta.author is not None
        ):
            results[url] = meta
    return results


__all__ = [
    "PageMetadata",
    "parse_page_date",
    "extract_metadata_from_html",
    "fetch_page_metadata",
]
