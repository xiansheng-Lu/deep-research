"""``POST /intent/classify`` API 集成测试（M2-1）。

通过 ``dependency_overrides`` 注入假 Session（仅用于鉴权加载用户），
``app.state.llm`` 注入替身 LLMClient；不发真实网络请求。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.intent import router as intent_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.models.identity import User
from app.orchestrator.schemas import IntentClassification
from app.provider.client import LLMClient, StructuredCompletion

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


class _FakeLLM(LLMClient):
    def __init__(self, result: IntentClassification) -> None:
        self._result = result
        self.last_text: str | None = None
        self.last_force_kwargs: dict[str, Any] = {}

    async def complete_structured(self, **kwargs: Any) -> StructuredCompletion:  # type: ignore[override]
        # user 消息即待判别文本
        self.last_text = kwargs["messages"][-1].content
        self.last_force_kwargs = kwargs
        return StructuredCompletion(parsed=self._result, usage={"total_tokens": 10}, model="fake", raw="")


def _build_app(llm: Any) -> FastAPI:
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(intent_router)

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


def test_classify_research_returns_recommendation(settings: Settings) -> None:
    llm = _FakeLLM(IntentClassification(intent="research", confidence=0.91, reason="对比分析"))  # type: ignore[arg-type]
    client = TestClient(_build_app(llm))

    resp = client.post(
        "/intent/classify",
        json={"text": "对比两类向量数据库在大规模检索场景下的性能与成本差异"},
        headers=_headers(settings),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "research"
    assert body["confidence"] == 0.91
    assert body["source"] == "llm"
    assert body["degraded"] is False
    assert body["recommended_template"] == "generic"
    assert body["recommended_tier"] in {"quick", "standard", "deep", "extreme"}
    assert isinstance(body["estimated_token_budget"], int)
    assert body["estimated_cost_grade"] == body["recommended_tier"]
    assert llm.last_text == "对比两类向量数据库在大规模检索场景下的性能与成本差异"


def test_classify_chat_has_null_research_params(settings: Settings) -> None:
    llm = _FakeLLM(IntentClassification(intent="chat", confidence=0.98, reason="问候"))  # type: ignore[arg-type]
    client = TestClient(_build_app(llm))

    resp = client.post("/intent/classify", json={"text": "你好呀"}, headers=_headers(settings))

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "chat"
    assert body["recommended_template"] is None
    assert body["recommended_tier"] is None
    assert body["estimated_token_budget"] is None
    assert body["estimated_cost_grade"] is None


def test_classify_force_bypasses_model(settings: Settings) -> None:
    llm = _FakeLLM(IntentClassification(intent="research", confidence=0.9, reason="x"))  # type: ignore[arg-type]
    client = TestClient(_build_app(llm))

    resp = client.post(
        "/intent/classify",
        json={"text": "随便聊聊", "force": "chat"},
        headers=_headers(settings),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "chat"
    assert body["source"] == "forced"
    assert body["confidence"] == 1.0
    # 强制路径不得调用模型
    assert llm.last_text is None


def test_classify_without_llm_degrades_to_research(settings: Settings) -> None:
    client = TestClient(_build_app(None))

    resp = client.post(
        "/intent/classify",
        json={"text": "任意研究问题"},
        headers=_headers(settings),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "research"
    assert body["source"] == "fallback"
    assert body["degraded"] is True
    assert body["estimated_token_budget"] is not None


def test_classify_requires_auth(settings: Settings) -> None:
    client = TestClient(_build_app(None))
    resp = client.post("/intent/classify", json={"text": "任意问题"})
    assert resp.status_code == 401


def test_classify_rejects_empty_text(settings: Settings) -> None:
    client = TestClient(_build_app(None))
    resp = client.post("/intent/classify", json={"text": ""}, headers=_headers(settings))
    assert resp.status_code == 422
