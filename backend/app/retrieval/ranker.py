"""证据相关性打分与排序（M2-6 T2）。

SDP §3 M2-6 要求「去重并按与子问题的相关性打分」。M1 的 ``rerank`` 为空占位、
``relevance_score`` 真链恒 0，导致可信分级公式把全部证据降两档（政府站也评 C）。

本模块为**零 token 的词面相关性**：

- 复用 critic 的中英混合切词口径（CJK 单字 + 拉丁连续段，见
  ``_tokenize``），保证同一证据在冲突检测与相关性打分中的词面口径一致；
- 由 query 词项在标题/正文的覆盖率 + 词袋 Jaccard 合成 0~1 分；
- Provider 自带相关性分（Tavily）与词面分 0.5/0.5 融合；博查不返回分数，
  直接用词面分。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.retrieval.base import RetrievalHit

# 参与 body 相似度的正文长度上限（与 critic/去重指纹口径一致，控成本）
_BODY_LIMIT = 2000

# 合成权重（评审锁定，修改需同步方案 §5.3 与单测）
_W_COVERAGE = 0.5
_W_TITLE = 0.3
_W_BODY = 0.2

# 中英混合词项：拉丁连续段按整段、CJK 按单字；与 critic._TOKEN_RE 同形
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")

# query 侧高频停用项：纯疑问/量词/时间通用词不参与覆盖率，避免「的/年/什么」
# 类字项把无关证据抬高（小而稳的中文停用字集合，不做完整分词）
_QUERY_STOP_CHARS: frozenset[str] = frozenset(
    {
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "及",
        "或",
        "对",
        "把",
        "被",
        "有",
        "什么",
        "怎么",
        "如何",
        "哪些",
        "请问",
        "一下",
        "这个",
        "那个",
        "我们",
        "他们",
        "可以",
        "需要",
        "进行",
        "相关",
        "关于",
        "对比",
        "分析",
        "情况",
        "方面",
        "问题",
        "年",
        "月",
        "中",
        "为",
        "等",
        "个",
    }
)


def _tokenize(text: str | None) -> set[str]:
    """中英混合切词：拉丁连续段整段、CJK 单字。"""
    if not text:
        return set()
    return set(_TOKEN_RE.findall(text.lower()))


def _query_terms(query: str) -> set[str]:
    """切分 query 并剔除停用项。"""
    return {token for token in _tokenize(query) if token not in _QUERY_STOP_CHARS and len(token) > 0}


def _coverage(terms: set[str], text: str | None) -> float:
    """query 词项在给定文本中的覆盖率（命中项 / query 有效项）。"""
    if not terms:
        return 0.0
    text_terms = _tokenize(text)
    hits = sum(1 for term in terms if term in text_terms)
    return hits / len(terms)


def _jaccard(a: set[str], b: set[str]) -> float:
    """词袋 Jaccard；任一为空返回 0。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_relevance(
    query: str,
    *,
    title: str | None,
    snippet: str | None,
    content: str | None = None,
) -> float:
    """计算一条证据相对于子问题 query 的词面相关性，返回 [0,1] 三位小数。

    query 无有效词项（全是停用字/空串）时返回 0.0，交由融合层按无分处理。
    """
    terms = _query_terms(query)
    if not terms:
        return 0.0
    body_text = f"{title or ''}\n{snippet or ''}\n{content or ''}"[:_BODY_LIMIT]
    # coverage：query 词项在证据整体文本中的覆盖率
    coverage = _coverage(terms, body_text)
    # title_hit：query 词项在标题中的覆盖率（标题命中权重单独计）
    title_hit = _coverage(terms, title)
    body_sim = _jaccard(terms, _tokenize(body_text))
    score = _W_COVERAGE * coverage + _W_TITLE * title_hit + _W_BODY * body_sim
    return round(min(1.0, max(0.0, score)), 3)


def blend_score(provider_score: float | None, lexical: float) -> float:
    """融合 Provider 相关性分与词面分。

    - Provider 有有效分（>0）：0.5/0.5 融合；
    - Provider 无分（博查恒 0、解析失败）：直接用词面分。
    """
    lexical = min(1.0, max(0.0, float(lexical or 0.0)))
    provider = float(provider_score or 0.0)
    if provider > 0.0:
        return round(min(1.0, 0.5 * provider + 0.5 * lexical), 3)
    return round(lexical, 3)


def score_hit(query: str, hit: RetrievalHit) -> float:
    """对单条 RetrievalHit 计算融合分（检索层/rerank 复用）。"""
    lexical = score_relevance(
        query,
        title=hit.title,
        snippet=hit.snippet,
        content=hit.content,
    )
    return blend_score(hit.score, lexical)


def rerank(hits: Sequence[RetrievalHit], query: str | None = None) -> list[RetrievalHit]:
    """按融合相关性分降序排序；无 query 时按 Provider 原始分排序。

    不改变输入集合（仅排序、不去重——去重由 ``retrieval.dedup`` 负责）。
    """
    if not hits:
        return []
    if query:
        return sorted(hits, key=lambda h: score_hit(query, h), reverse=True)
    return sorted(hits, key=lambda h: float(h.score or 0.0), reverse=True)


__all__ = [
    "score_relevance",
    "blend_score",
    "score_hit",
    "rerank",
]
