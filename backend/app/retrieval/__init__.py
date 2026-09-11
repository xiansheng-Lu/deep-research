"""检索模块：web 检索、结果排序、正文抽取、去重、门面。"""

from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.bocha import BochaProvider, build_bocha_provider
from app.retrieval.client import RetrievalClient, get_default_client
from app.retrieval.dedup import dedupe, fingerprint, merge_unique, normalize_url
from app.retrieval.extractor import extract_text, extract_text_from_html
from app.retrieval.web_search import (
    TavilyProvider,
    WebSearchProvider,
    build_tavily_provider,
)

__all__ = [
    "BochaProvider",
    "RetrievalClient",
    "RetrievalHit",
    "RetrievalRequest",
    "RetrievalSource",
    "TavilyProvider",
    "WebSearchProvider",
    "build_bocha_provider",
    "build_tavily_provider",
    "dedupe",
    "extract_text",
    "extract_text_from_html",
    "fingerprint",
    "get_default_client",
    "merge_unique",
    "normalize_url",
]
