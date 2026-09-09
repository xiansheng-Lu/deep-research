"""向量嵌入占位：M1 阶段接 pgvector 与嵌入 Provider。"""

from app.knowledge.base import KnowledgeItem


class EmbeddingEncoder:
    """嵌入编码器占位。"""

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """占位：返回与输入等长的零向量，避免下游报错。"""
        return [[0.0] * 8 for _ in texts]

    async def encode_item(self, item: KnowledgeItem) -> list[float]:
        return (await self.encode([item.content]))[0]