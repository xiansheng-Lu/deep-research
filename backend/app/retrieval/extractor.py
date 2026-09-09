"""正文抽取占位：M1 阶段使用 trafilatura / readability-lxml 抽取正文。"""

from app.retrieval.base import RetrievalHit


async def extract_text(hit: RetrievalHit) -> str:
    """占位实现：返回 snippet。"""
    return hit.snippet