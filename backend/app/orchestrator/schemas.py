"""节点侧 LLM 结构化输出契约（Pydantic 模型）。

本模块集中定义各节点向 LLM 索取的 JSON Schema。
LLD §6.5.2 / §6.5.3 / §6.5.5 / §6.5.6 等章节引用的 Schema 全部在此声明。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 入站结构化输出统一采用 extra="ignore"：
# DeepSeek 等兼容 OpenAI 接口的模型在 json_object 模式下只保证输出合法 JSON，
# 不保证字段闭合（实测会回显 {"type": "json_object"} 等多余字段）；
# 这些模型仅用于解析外部 LLM 输出，忽略多余字段即可，缺失必填字段仍会校验失败。
_LLM_OUTPUT_CONFIG = ConfigDict(extra="ignore")


class ClarificationQuestion(BaseModel):
    """单条澄清问题（§6.5.2）。

    Attributes:
        key: 答案回写时使用的字段名（例如 ``"scope"``）。
        text: 面向用户展示的完整问句。
        options: 候选选项；为空表示自由文本输入。
        recommended: 推荐选项在 ``options`` 中的索引；None 表示无偏好。
    """

    model_config = _LLM_OUTPUT_CONFIG

    key: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(default_factory=list)
    recommended: int | None = None


class ClarificationSchema(BaseModel):
    """澄清节点结构化输出（§6.5.2）。

    Attributes:
        requires_user_input: 是否需要用户追问后再继续。
        questions: 追问问题列表。
        defaults: 当 ``requires_user_input`` 为 False 时使用的默认结构化字段。
        structured_question: 当 ``requires_user_input`` 为 False 时的目标/范围/关键概念等结构化查询。
    """

    model_config = _LLM_OUTPUT_CONFIG

    requires_user_input: bool
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    structured_question: dict[str, Any] = Field(default_factory=dict)


class SubQuestionItem(BaseModel):
    """单条子问题（§6.5.3）。

    Attributes:
        question: 子问题完整问题。
        depends_on: 依赖的上游子问题 ID（拓扑顺序执行用）。
        rationale: 拆解理由（仅用于审计，前端不展示）。
    """

    model_config = _LLM_OUTPUT_CONFIG

    question: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=500)


class SubQuestionListSchema(BaseModel):
    """子问题列表（§6.5.3）。"""

    model_config = _LLM_OUTPUT_CONFIG

    sub_questions: list[SubQuestionItem] = Field(default_factory=list)


class IntentClassification(BaseModel):
    """意图路由 LLM 结构化输出（PRD 模块 G / 智能体协作规格 §3.1）。

    Attributes:
        intent: 意图类别——chat 闲聊直答 / research 深度研究 / uncertain 无法确定。
        confidence: 模型置信度，0-1。
        reason: 简短判定依据（仅排查用，前端不作为主文案）。
    """

    model_config = _LLM_OUTPUT_CONFIG

    intent: Literal["chat", "research", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=300)


__all__ = [
    "ClarificationQuestion",
    "ClarificationSchema",
    "IntentClassification",
    "SubQuestionItem",
    "SubQuestionListSchema",
]
