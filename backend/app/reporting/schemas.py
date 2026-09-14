"""Reporter 结构化输出的内部 LLM Schema（M2-7）。

LLM 只允许产出 conclusion/evidence/limitation 三类区块草稿；dispute 块不经模型
产出，由绑定引擎按冲突状态机确定性注入（技术方案 §5.6）。模型仅用于
``LLMClient.complete_structured`` 解析校验，不直接对外暴露。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 单块文本上限：1~3 句中文，800 字足以容纳，超长视为脏返回走降级
LLM_BLOCK_TEXT_MAX = 800

LLMBlockType = Literal["conclusion", "evidence", "limitation"]
LLMConfidence = Literal["single_source", "cross_verified", "inferred"]


class LLMBlockDraftModel(BaseModel):
    """LLM 产出的单块草稿（引用仅给证据 id 与可选原句，snippet 一律后端取）。"""

    type: LLMBlockType
    text: str = Field(min_length=1, max_length=LLM_BLOCK_TEXT_MAX)
    confidence: LLMConfidence
    # 只能引用当 run 证据池内的 id；越界 id 由绑定引擎白名单剔除并计数
    evidence_ids: list[str] = Field(default_factory=list)
    # 可选：论断依据的证据原句，键为 evidence_id；引擎做正文子串校验
    quotes: dict[str, str] = Field(default_factory=dict)


class LLMReportPlan(BaseModel):
    """Reporter 单次结构化调用的完整计划。"""

    blocks: list[LLMBlockDraftModel] = Field(min_length=1)


__all__ = [
    "LLM_BLOCK_TEXT_MAX",
    "LLMBlockDraftModel",
    "LLMBlockType",
    "LLMConfidence",
    "LLMReportPlan",
]
