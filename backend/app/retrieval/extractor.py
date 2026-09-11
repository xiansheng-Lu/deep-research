"""正文抽取：trafilatura 为主、readability-lxml 兜底。

M1 阶段 ``extract_text`` 接收 ``RetrievalHit``，返回从 ``content`` 或外部抓取的纯文本；
M2 阶段可接入爬虫代理与质量打分。
"""

from __future__ import annotations

import logging

import trafilatura
from readability import Document

from app.retrieval.base import RetrievalHit

log = logging.getLogger("retrieval.extractor")


def _truncate_to_byte(text: str, max_bytes: int = 200_000) -> str:
    """避免 trafilatura 处理超长文本时的内存膨胀；按字节截断。"""
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _trafilatura_extract(html: str) -> str:
    """trafilatura 抽取正文，失败返回空串。"""
    try:
        text = trafilatura.extract(
            _truncate_to_byte(html),
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            with_metadata=False,
        )
    except Exception as exc:  # noqa: BLE001 - trafilatura 异常时降级到 readability
        log.warning("trafilatura.extract 异常: %s", exc)
        return ""
    return text or ""


def _readability_extract(html: str) -> str:
    """readability-lxml 抽取正文，作为 trafilatura 的备选。"""
    try:
        doc = Document(_truncate_to_byte(html))
        summary = doc.summary(html_partial=True)
        if not summary:
            return ""
        # readability 输出 HTML，需要剥掉标签
        from lxml import html as lxml_html

        return lxml_html.fromstring(summary).text_content()
    except Exception as exc:  # noqa: BLE001 - readability 失败则视为空
        log.warning("readability.Document 异常: %s", exc)
        return ""


def extract_text_from_html(html: str) -> str:
    """从原始 HTML 抽取正文：trafilatura 优先，readability 兜底。"""
    if not html:
        return ""
    text = _trafilatura_extract(html)
    if text:
        return text.strip()
    text = _readability_extract(html)
    return text.strip() if text else ""


async def extract_text(hit: RetrievalHit) -> str:
    """占位/兜底实现：当前 ``RetrievalHit.content`` 由 Provider 在 search/extract 阶段填入。

    - 若 ``hit.content`` 已有（非空），直接返回。
    - 若为空但 ``hit.raw`` 含 ``html``，走 ``extract_text_from_html``。
    - 否则返回 ``hit.snippet``。
    """
    if hit.content:
        return hit.content
    raw = hit.raw or {}
    html = raw.get("html") if isinstance(raw, dict) else None
    if isinstance(html, str) and html:
        return extract_text_from_html(html)
    return hit.snippet
