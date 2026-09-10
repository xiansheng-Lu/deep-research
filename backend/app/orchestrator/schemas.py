"""节点侧 LLM 结构化输出契约（Pydantic 模型）。

本模块集中定义各节点向 LLM 索取的 JSON Schema。
LLD §6.5.2 / §6.5.3 / §6.5.5 / §6.5.6 等章节引用的 Schema 全部在此声明。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ClarificationQuestion(BaseModel):
    """单条澄清问题（§6.5.2）。

    Attributes:
        key: 答案回写时使用的字段名（例如 ``"scope"``）。
        text: 面向用户展示的完整问句。
        options: 候选选项；为空表示自由文本输入。
        recommended: 推荐选项在 ``options`` 中的索引；None 表示无偏好。
    """

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    requires_user_input: bool
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    structured_question: dict[str, Any] = Field(default_factory=dict)


class SubQuestionItem(BaseModel):
    """单条子问题（§6.5.3）。

    Attributes:
        question: 子问题完整文本。
        depends_on: 依赖的上游子问题 ID（拓扑顺序执行用）。
        rationale: 拆解理由（仅用于审计，前端不展示）。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=500)


class SubQuestionListSchema(BaseModel):
    """子问题列表（§6.5.3）。"""

    model_config = ConfigDict(extra="forbid")

    sub_questions: list[SubQuestionItem] = Field(default_factory=list)


__all__ = [
    "ClarificationQuestion",
    "ClarificationSchema",
    "SubQuestionItem",
    "SubQuestionListSchema",
]
