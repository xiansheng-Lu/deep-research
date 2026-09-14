"""M2-8a 埋点接收端点与条级清洗/采样/限流的 API 测试（假会话，无网络）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.telemetry import router as telemetry_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.models.identity import User

_USER_ID = "01JATESTUSER000000000000001"
_TEAM_ID = "01JATESTTEAM0000000000000001"


def _make_user() -> User:
    now = datetime.now(tz=UTC)
    return User(
        id=_USER_ID,
        team_id=_TEAM_ID,
        email="t@example.com",
        hashed_password="x",
        display_name="埋点测试用户",
        role="owner",
        created_at=now,
        updated_at=now,
    )


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add_all(self, objs: list[Any]) -> None:
        self.added.extend(objs)

    async def commit(self) -> None:
        return None

    async def scalars(self, _stmt: Any) -> Any:
        class _R:
            def all(self_inner) -> list[Any]:
                return []

        return _R()


def _build_app(settings: Settings, session: _FakeSession) -> FastAPI:
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(telemetry_router)

    async def _fake_db_session():
        yield session

    app.dependency_overrides[api_deps.db_session] = _fake_db_session

    @app.exception_handler(AppError)
    async def _handler(_request, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return app


def _event(**over: Any) -> dict[str, Any]:
    base = {
        "event": "report.citation.open",
        "ts": int(datetime.now(tz=UTC).timestamp() * 1000),
        "run_id": "run-1",
        "page": "/report",
        "props": {"evidence_id": "ev-1"},
    }
    base.update(over)
    return base


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = get_settings()
    monkeypatch.setattr(s, "telemetry_sample_rate", 1.0)
    monkeypatch.setattr(s, "telemetry_rate_limit_per_min", 100)
    return s


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


def _override_user(app: FastAPI) -> None:
    async def _fake_current_user():
        return _make_user()

    app.dependency_overrides[api_deps.current_user] = _fake_current_user


@pytest.fixture
def authed_client(settings: Settings, session: _FakeSession) -> TestClient:
    ingest_mod = __import__("app.telemetry_ingest", fromlist=["_rate_limiter"])
    ingest_mod._rate_limiter = None
    app = _build_app(settings, session)
    _override_user(app)
    return TestClient(app, raise_server_exceptions=True)


class TestBatchIngest:
    def test_valid_batch_persists_with_injected_identity(
        self, authed_client: TestClient, session: _FakeSession
    ) -> None:
        resp = authed_client.post(
            "/telemetry/batch",
            json={"events": [_event(), _event(event="intent.classify", run_id=None)]},
        )
        assert resp.status_code == 204
        assert resp.headers.get("X-Accepted-Events") == "2"
        assert len(session.added) == 2
        row = session.added[0]
        assert row.user_id == _USER_ID and row.team_id == _TEAM_ID
        assert row.event == "report.citation.open"
        assert row.run_id == "run-1"
        assert row.props == {"evidence_id": "ev-1"}

    def test_invalid_events_dropped_valid_kept(
        self, authed_client: TestClient, session: _FakeSession
    ) -> None:
        old_ts = int((datetime.now(tz=UTC).timestamp() - 30 * 86400) * 1000)
        resp = authed_client.post(
            "/telemetry/batch",
            json={
                "events": [
                    _event(),
                    _event(event="BAD-NAME"),  # 非法字符
                    _event(ts=old_ts),  # 超 ±7 天
                    _event(props={"nested": {"a": 1}}),  # 非标量
                    _event(props={"k": "x" * 300}),  # 字符串超长
                    _event(page="x" * 200),  # page 超长
                ]
            },
        )
        assert resp.status_code == 204
        assert resp.headers["X-Accepted-Events"] == "1"
        assert len(session.added) == 1

    def test_all_invalid_still_204(self, authed_client: TestClient, session: _FakeSession) -> None:
        resp = authed_client.post(
            "/telemetry/batch",
            json={"events": [_event(event="BAD-NAME")]},
        )
        assert resp.status_code == 204
        assert resp.headers["X-Accepted-Events"] == "0"
        assert session.added == []

    def test_batch_level_422(self, authed_client: TestClient, session: _FakeSession) -> None:
        # 空批（Pydantic min_length）→ FastAPI 请求体 422，整批不入库
        resp = authed_client.post("/telemetry/batch", json={"events": []})
        assert resp.status_code == 422
        assert session.added == []

    def test_oversized_count_422(self, authed_client: TestClient, session: _FakeSession) -> None:
        resp = authed_client.post("/telemetry/batch", json={"events": [_event() for _ in range(201)]})
        assert resp.status_code == 422
        assert session.added == []

    def test_missing_event_field_422(self, authed_client: TestClient, session: _FakeSession) -> None:
        resp = authed_client.post("/telemetry/batch", json={"events": [{"ts": 123}]})
        assert resp.status_code == 422
        assert session.added == []

    def test_unauthenticated_401(self, settings: Settings, session: _FakeSession) -> None:
        app = _build_app(settings, session)
        c = TestClient(app)
        resp = c.post("/telemetry/batch", json={"events": [_event()]})
        assert resp.status_code == 401

    def test_rate_limited_429(self, monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
        s = get_settings()
        monkeypatch.setattr(s, "telemetry_sample_rate", 1.0)
        monkeypatch.setattr(s, "telemetry_rate_limit_per_min", 2)
        ingest_mod = __import__("app.telemetry_ingest", fromlist=["_rate_limiter"])
        ingest_mod._rate_limiter = None
        app = _build_app(s, session)
        _override_user(app)
        c = TestClient(app)
        body = {"events": [_event()]}
        assert c.post("/telemetry/batch", json=body).status_code == 204
        assert c.post("/telemetry/batch", json=body).status_code == 204
        third = c.post("/telemetry/batch", json=body)
        assert third.status_code == 429
        assert third.json()["code"] == "telemetry_rate_limited"

    def test_sample_zero_drops_all(self, monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
        s = get_settings()
        monkeypatch.setattr(s, "telemetry_sample_rate", 0.0)
        monkeypatch.setattr(s, "telemetry_rate_limit_per_min", 100)
        ingest_mod = __import__("app.telemetry_ingest", fromlist=["_rate_limiter"])
        ingest_mod._rate_limiter = None
        app = _build_app(s, session)
        _override_user(app)
        c = TestClient(app)
        resp = c.post("/telemetry/batch", json={"events": [_event(), _event()]})
        assert resp.status_code == 204
        assert resp.headers["X-Accepted-Events"] == "0"
        assert session.added == []
