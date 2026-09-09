"""文档分块占位：M1 阶段实现基于句子 / 段落 + token 长度的滑动窗口。"""


def chunk_text(text: str, *, max_tokens: int = 500, overlap: int = 64) -> list[str]:
    """占位：按段落切分，不做精细分块。"""
    if not text:
        return []
    return [p for p in text.split("\n\n") if p.strip()]