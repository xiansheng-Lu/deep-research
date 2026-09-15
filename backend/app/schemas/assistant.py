"""全局助手（闲聊）域 Pydantic Schema（前端详设 §12.6 / PRD 模块 G）。

M2-1 不做服务端会话持久化：历史由客户端随请求携带，服务端无状态。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AssistantRole = Literal["user", "assistant"]

# 单次请求最多携带的历史消息条数（10 轮 = 20 条）
_MAX_HISTORY_ITEMS = 20
# 单条消息与当前提问的长度上限
_MAX_TURN_CHARS = 4000
_MAX_MESSAGE_CHARS = 2000


class AssistantTurn(BaseModel):
    """历史对话中的一条消息。"""

    role: AssistantRole
    content: str = Field(min_length=1, max_length=_MAX_TURN_CHARS)


class AssistantChatRequest(BaseModel):
    """``POST /assistant/chat`` 请求体。"""

    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS, description="当前用户消息")
    # 客户端携带的最近对话历史，服务端不落库
    history: list[AssistantTurn] = Field(default_factory=list, max_length=_MAX_HISTORY_ITEMS)


__all__ = [
    "AssistantChatRequest",
    "AssistantRole",
    "AssistantTurn",
]
