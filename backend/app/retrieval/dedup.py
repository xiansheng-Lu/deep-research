"""检索去重：URL 规范化与 fingerprint 哈希。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.retrieval.base import RetrievalHit

# 常见无意义 query 参数（utm_* / 跟踪 / 推荐来源）
_DROPPED_PARAMS_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_")
_DROPPED_PARAMS_EXACT = {"source", "medium", "campaign", "term", "content", "from", "share_source"}


def normalize_url(url: str) -> str:
    """规范化 URL：去 fragment、小写 scheme/host、排序 & 过滤 query 参数。

    用于在 URL 层面判断"是否同一份内容"；content 哈希在 fingerprint 中兜底。
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www.") and len(netloc) > 4:
        netloc = netloc[4:]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    filtered: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lk = key.lower()
        if lk in _DROPPED_PARAMS_EXACT:
            continue
        if any(lk.startswith(p) for p in _DROPPED_PARAMS_PREFIXES):
            continue
        filtered.append((key, value))
    filtered.sort()
    query = urlencode(filtered, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def fingerprint(
    hit: RetrievalHit,
    *,
    content_limit: int = 2000,
) -> str:
    """基于规范化 URL + 截断内容生成 SHA-256 fingerprint。"""
    url_key = normalize_url(hit.url or "") if hit.url else ""
    body = hit.content or hit.snippet or ""
    body = body[:content_limit]
    digest = hashlib.sha256()
    digest.update(url_key.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(body.encode("utf-8"))
    return digest.hexdigest()


def dedupe(
    hits: Sequence[RetrievalHit],
    *,
    keep_highest_score: bool = True,
) -> list[RetrievalHit]:
    """按 fingerprint 去重；同 fingerprint 保留 score 最高（或首次出现）的一条。"""
    if not hits:
        return []
    seen: dict[str, RetrievalHit] = {}
    for hit in hits:
        key = fingerprint(hit)
        if key not in seen:
            seen[key] = hit
            continue
        if keep_highest_score and hit.score > seen[key].score:
            seen[key] = hit
    return list(seen.values())


def merge_unique(*groups: Iterable[RetrievalHit]) -> list[RetrievalHit]:
    """合并多组命中并去重，便于 fan-out 后聚合。"""
    merged: list[RetrievalHit] = []
    for group in groups:
        merged.extend(group)
    return dedupe(merged)
