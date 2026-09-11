"""检索公共数据结构。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class RetrievalSource(StrEnum):
    """检索来源。"""

    WEB = "web"
    KNOWLEDGE = "knowledge"
    CONNECTOR = "connector"


@dataclass(slots=True)
class RetrievalRequest:
    """检索请求。

    Attributes:
        query: 检索查询语句。
        top_k: 期望返回条数上限。
        recency_days: 内容新鲜度约束（None 表示不限制）。
        sources: 来源过滤；空列表表示全部来源。
    """

    query: str
    top_k: int = 10
    recency_days: int | None = None
    sources: list[RetrievalSource] = field(default_factory=list)


@dataclass(slots=True)
class RetrievalHit:
    """单条检索结果。

    Attributes:
        source: 检索来源。
        title: 标题。
        url: 来源 URL（可能为 None，例如本地知识条目）。
        snippet: 摘要/搜索片段。
        content: 抽取后的正文（extract 阶段填充）。
        score: 相关性分数（0~1）。
        published_at: 内容发布时间（可能为 None）。
        fetched_at: 抓取时间。
        raw: Provider 原始返回数据，便于回溯。
    """

    source: RetrievalSource
    title: str
    url: str | None
    snippet: str
    score: float
    content: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    raw: dict[str, object] | None = None
