"""知识库公共数据结构。"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class KnowledgeItem:
    """知识库条目。"""

    id: UUID
    project_id: UUID
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KnowledgeSearchRequest:
    """向量检索请求。"""

    project_id: UUID
    query: str
    top_k: int = 10
    filter_tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class KnowledgeSearchResult:
    """向量检索结果。"""

    item: KnowledgeItem
    score: float
    chunk: str | None = None