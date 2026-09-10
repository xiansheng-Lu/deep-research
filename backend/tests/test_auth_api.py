"""``/auth/*`` API 集成测试。

策略：通过 ``dependency_overrides`` 把 ``db_session`` 替换为内存假 Session，
让 ``current_user`` 走真实 JWT 解码逻辑。覆盖：

- 受保护路由无 token → 401
- 受保护路由错 token → 401
- 受保护路由 type=refresh token → 401
- ``/auth/me`` 用合法 access token → 200 + 用户视图
- ``/auth/logout`` 合法 token → 200
- ``/auth/refresh`` 合法 refresh token → 新 access token
- ``/auth/refresh`` 用 access token 当 refresh → 401
- ``/auth/login`` 不存在的邮箱 → 401
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.auth import router as auth_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token, create_refresh_token
from app.db.models.identity import User


# ====== 假 User + 假 Session ======

_FAKE_USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"  # 26-char ULID 格式
_FAKE_TEAM_ID = "01HZX7YK9P3M2V4N5J8XQRCWT7"


def _make_fake_user() -> User:
    """构造一个未持久化的 User 实例（不进 DB）。"""
    return User(
        id=_FAKE_USER_ID,
        team_id=_FAKE_TEAM_ID,
        email="alice@example.com",
        hashed_password="$2b$12$dummy",
        display_name="Alice",
        role="researcher",
    )


class _FakeAsyncSession:
    """最小 AsyncSession 替身：仅支持 ``scalar`` 与 ``flush``。"""

    def __init__(self, user: User | None) -> None:
        self._user = user

    async def scalar(self, _stmt: Any) -> User | None:
        return self._user

    async def flush(self) -> None:
        return None


def _build_test_app(settings: Settings, session: _FakeAsyncSession) -> FastAPI:
    """构造最小 FastAPI app：挂 auth 路由 + 全局异常处理器 + 依赖覆盖。"""
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(auth_router)

    # 只覆盖 db_session；current_user 走真实 JWT 解码 + 查 User
    async def _fake_db_session():
        yield session

    app.dependency_overrides[api_deps.db_session] = _fake_db_session

    # 全局异常处理器（与 app.main 对齐）
    @app.exception_handler(AppError)
    async def _app_error_handler(_request, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


# ====== Fixtures ======

@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def client_with_user(settings: Settings) -> TestClient:
    user = _make_fake_user()
    session = _FakeAsyncSession(user=user)
    app = _build_test_app(settings, session)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def client_without_user(settings: Settings) -> TestClient:
    """未登录用户场景：``session.scalar`` 返回 None。"""
    session = _FakeAsyncSession(user=None)
    app = _build_test_app(settings, session)
    return TestClient(app, raise_server_exceptions=True)


# ====== 测试用例 ======


def test_me_without_token_returns_401(client_with_user: TestClient) -> None:
    """受保护路由缺少 Bearer 应返回 401。"""
    resp = client_with_user.get("/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "auth_error"


def test_me_with_invalid_token_returns_401(client_with_user: TestClient) -> None:
    """错签名/伪 token 应 401。"""
    resp = client_with_user.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_me_with_refresh_token_returns_401(settings: Settings, client_with_user: TestClient) -> None:
    """用 refresh token 访问 access 路由应 401（type 校验失败）。"""
    refresh = create_refresh_token(_FAKE_USER_ID, settings=settings)
    resp = client_with_user.get("/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert resp.status_code == 401
    assert "令牌类型错误" in resp.json()["message"]


def test_me_with_valid_token_returns_200(settings: Settings, client_with_user: TestClient) -> None:
    """合法 access token 应返回当前用户视图。"""
    access = create_access_token(_FAKE_USER_ID, settings=settings)
    resp = client_with_user.get(
        "/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == _FAKE_USER_ID
    assert body["team_id"] == _FAKE_TEAM_ID
    assert body["email"] == "alice@example.com"
    assert body["role"] == "researcher"


def test_login_unknown_email_returns_401(client_without_user: TestClient) -> None:
    """不存在的邮箱应返回 401（不区分用户不存在 vs 密码错）。"""
    resp = client_without_user.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "any"}
    )
    assert resp.status_code == 401


def test_refresh_with_access_token_returns_401(settings: Settings, client_with_user: TestClient) -> None:
    """用 access token 当 refresh 应 401。"""
    access = create_access_token(_FAKE_USER_ID, settings=settings)
    resp = client_with_user.post("/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


def test_refresh_with_valid_refresh_returns_new_tokens(
    settings: Settings, client_with_user: TestClient
) -> None:
    """合法 refresh token 应换发新 access token。"""
    refresh = create_refresh_token(_FAKE_USER_ID, settings=settings)
    resp = client_with_user.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] != refresh
    assert body["expires_in"] == settings.jwt_access_ttl_minutes * 60


def test_logout_with_valid_token_returns_200(settings: Settings, client_with_user: TestClient) -> None:
    """合法 token 登出应返回 200。"""
    access = create_access_token(_FAKE_USER_ID, settings=settings)
    resp = client_with_user.post(
        "/auth/logout", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_does_not_require_auth(client_with_user: TestClient) -> None:
    """健康检查不依赖 auth。"""
    resp = client_with_user.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
