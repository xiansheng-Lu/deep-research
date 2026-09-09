"""知识库存储占位：M1 阶段落 ORM（KnowledgeItem / KnowledgeEmbedding）。"""

from app.knowledge.base import KnowledgeItem


class KnowledgeStore:
    """知识库存储门面占位。"""

    def __init__(self) -> None:
        self._items: dict[str, KnowledgeItem] = {}

    async def upsert(self, item: KnowledgeItem) -> KnowledgeItem:
        self._items[str(item.id)] = item
        return item

    async def get(self, item_id: str) -> KnowledgeItem | None:
        return self._items.get(item_id)

    async def list_for_project(self, project_id: str) -> list[KnowledgeItem]:
        return [it for it in self._items.values() if str(it.project_id) == project_id]