"""``POST /assistant/chat`` SSE 接口测试（M2-1）。

覆盖：SSE 帧协议（delta + [DONE]）、历史消息上送、未认证 401、
LLM 未配置 503、入参校验 422、流中异常 error 帧。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.assistant import router as assistant_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.models.identity import User
from app.provider.base import ChatRequest
from app.provider.client import LLMClient

_FAKE_USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"


def _make_fake_user() -> User:
    return User(
        id=_FAKE_USER_ID,
        team_id="01HZX7TEAM0000000000000001",
        email="alice@example.com",
        hashed_password="$2b$12$dummy",
        display_name="Alice",
        role="researcher",
    )


class _FakeSession:
    async def scalar(self, _stmt: Any) -> User:
        return _make_fake_user()


class _StreamingLLM(LLMClient):
    """按固定片段流式返回，并记录组装后的消息。"""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.requests: list[ChatRequest] = []

    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        for chunk in self._chunks:
            yield chunk


class _BoomStreamLLM(LLMClient):
    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        yield "半句"
        raise RuntimeError("connection reset")
        yield ""  # pragma: no cover - 保证生成器类型


def _build_app(llm: Any) -> FastAPI:
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(assistant_router)

    async def _fake_db_session():
        yield _FakeSession()

    app.dependency_overrides[api_deps.db_session] = _fake_db_session
    app.state.llm = llm

    @app.exception_handler(AppError)
    async def _app_error_handler(_request, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return app


@pytest.fixture
def settings() -> Settings:
    return get_settings()


def _headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(_FAKE_USER_ID, settings=settings)}"}


def test_chat_streams_delta_frames_and_done(settings: Settings) -> None:
    llm = _StreamingLLM(["你好", "，有什么", "可以帮你？"])
    client = TestClient(_build_app(llm))

    resp = client.post(
        "/assistant/chat",
        json={"message": "你好"},
        headers=_headers(settings),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert 'data: {"delta":"你好"}' in body
    assert 'data: {"delta":"，有什么"}' in body
    assert 'data: {"delta":"可以帮你？"}' in body
    assert body.rstrip().endswith("data: [DONE]")
    # 系统提示 + 单条用户消息，无历史时不产生额外消息
    messages = llm.requests[0].messages
    assert messages[0].role == "system"
    assert messages[-1].role == "user" and messages[-1].content == "你好"
    assert len(messages) == 2


def test_chat_history_is_passed_to_llm(settings: Settings) -> None:
    llm = _StreamingLLM(["ok"])
    client = TestClient(_build_app(llm))

    resp = client.post(
        "/assistant/chat",
        json={
            "message": "再展开说说",
            "history": [
                {"role": "user", "content": "什么是 RAG"},
                {"role": "assistant", "content": "RAG 是检索增强生成"},
            ],
        },
        headers=_headers(settings),
    )

    assert resp.status_code == 200
    roles = [m.role for m in llm.requests[0].messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert llm.requests[0].messages[1].content == "什么是 RAG"
    assert llm.requests[0].messages[-1].content == "再展开说说"


def test_chat_requires_auth(settings: Settings) -> None:
    client = TestClient(_build_app(_StreamingLLM(["x"])))
    resp = client.post("/assistant/chat", json={"message": "你好"})
    assert resp.status_code == 401


def test_chat_without_llm_returns_503(settings: Settings) -> None:
    client = TestClient(_build_app(None))
    resp = client.post("/assistant/chat", json={"message": "你好"}, headers=_headers(settings))
    assert resp.status_code == 503
    assert resp.json()["code"] == "provider_unavailable"


def test_chat_rejects_empty_message(settings: Settings) -> None:
    client = TestClient(_build_app(_StreamingLLM(["x"])))
    resp = client.post("/assistant/chat", json={"message": ""}, headers=_headers(settings))
    assert resp.status_code == 422


def test_chat_stream_error_emits_error_frame(settings: Settings) -> None:
    client = TestClient(_build_app(_BoomStreamLLM()))
    resp = client.post("/assistant/chat", json={"message": "说点什么"}, headers=_headers(settings))

    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert "provider_unavailable" in resp.text
    assert "[DONE]" not in resp.text
