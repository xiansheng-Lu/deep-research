"""结果排序占位：M1 阶段实现基于相关度 / 时效 / 来源权威性的融合排序。"""

from app.retrieval.base import RetrievalHit


def rerank(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """占位：当前直接返回原始顺序。"""
    return hits