"""检索公共数据结构。"""

from dataclasses import dataclass, field
from enum import StrEnum


class RetrievalSource(StrEnum):
    """检索来源。"""

    WEB = "web"
    KNOWLEDGE = "knowledge"
    CONNECTOR = "connector"


@dataclass(slots=True)
class RetrievalRequest:
    """检索请求。"""

    query: str
    top_k: int = 10
    recency_days: int | None = None
    sources: list[RetrievalSource] = field(default_factory=list)


@dataclass(slots=True)
class RetrievalHit:
    """单条检索结果。"""

    source: RetrievalSource
    title: str
    url: str | None
    snippet: str
    score: float
    raw: dict[str, object] | None = None