"""全局助手闲聊接口（PRD 模块 G / 前端详设 §12.6）。

``POST /assistant/chat``：意图判为 chat 的输入在此由通用 LLM 直接流式作答，
不走六阶段研究流水线、不检索、不产生项目数据。M2-1 无服务端会话存储，
历史由客户端随请求携带。

响应为 SSE（``text/event-stream``）：
- 增量帧：``data: {"delta": "文本片段"}\\n\\n``
- 结束帧：``data: [DONE]\\n\\n``
- 异常帧：``event: error\\ndata: {"code": "...", "message": "..."}\\n\\n``
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.core.exceptions import ProviderUnavailableError
from app.provider.base import ChatMessage, ChatRequest
from app.provider.client import LLMClient
from app.schemas.assistant import AssistantChatRequest

router = APIRouter(prefix="/assistant", tags=["assistant"])

# 闲聊回答上限与采样温度（不做研究，回答宜简短自然）
_CHAT_MAX_TOKENS = 1000
_CHAT_TEMPERATURE = 0.7

_SYSTEM_PROMPT_ZH = (
    "你是用户的通用 AI 助手，在独立的闲聊面板中直接回答用户问题。"
    "要求：用语简洁友好；只基于自身知识作答，不要声称进行了联网检索或引用外部来源；"
    "不发起研究、不输出研究报告结构；遇到需要实时信息或多源核验才能回答的问题，"
    "简要说明局限并建议用户改用深度研究。"
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_data(payload: str) -> str:
    return f"data: {payload}\n\n"


def _build_messages(payload: AssistantChatRequest) -> list[ChatMessage]:
    """系统提示 + 客户端历史 + 当前消息。"""
    messages = [ChatMessage(role="system", content=_SYSTEM_PROMPT_ZH)]
    messages.extend(ChatMessage(role=turn.role, content=turn.content) for turn in payload.history)
    messages.append(ChatMessage(role="user", content=payload.message))
    return messages


async def _stream_answer(llm: LLMClient, payload: AssistantChatRequest) -> AsyncIterator[str]:
    request = ChatRequest(
        messages=_build_messages(payload),
        temperature=_CHAT_TEMPERATURE,
        max_tokens=_CHAT_MAX_TOKENS,
    )
    try:
        async for chunk in llm.stream(request):
            if chunk:
                yield _sse_data(json.dumps({"delta": chunk}, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:  # noqa: BLE001 - 流式中断以错误帧告知客户端
        error_payload = json.dumps(
            {"code": "provider_unavailable", "message": f"回答中断：{type(exc).__name__}"},
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {error_payload}\n\n"
        return
    yield _sse_data("[DONE]")


@router.post("/chat", summary="闲聊直答（SSE 流式）")
async def chat(
    payload: AssistantChatRequest,
    request: Request,
    current_user: CurrentUser,
) -> StreamingResponse:
    """通用 LLM 流式直答；LLM 未配置时返回 503。"""
    del current_user  # 仅要求登录态
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise ProviderUnavailableError("LLM 未配置，闲聊服务暂不可用")

    return StreamingResponse(
        _stream_answer(llm, payload),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
