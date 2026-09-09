"""Web 检索占位（M1 接入 Tavily / SerpAPI 等）。"""

from app.retrieval.base import RetrievalHit, RetrievalRequest


async def search(req: RetrievalRequest) -> list[RetrievalHit]:
    """占位实现：M1 阶段接入 Tavily SDK 后返回真实结果。"""
    return []