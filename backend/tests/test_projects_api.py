"""``/projects`` API 集成测试。

策略：通过 ``dependency_overrides`` 把 ``db_session`` 替换为内存假 Session，
让 ``current_user`` 走真实 JWT 解码逻辑。覆盖：

- ``GET /projects`` 无 token → 401
- ``GET /projects`` 合法 token → 200 + 项目列表
- ``POST /projects`` 合法 token → 201 + 创建的项目
- ``POST /projects`` 无 token → 401
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.projects import router as projects_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.base import new_ulid
from app.db.models.identity import User
from app.db.models.project import Project

_FAKE_USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"
_FAKE_TEAM_ID = "01HZX7YK9P3M2V4N5J8XQRCWT7"


def _make_fake_user() -> User:
    """构造一个未持久化的 User 实例。"""
    return User(
        id=_FAKE_USER_ID,
        team_id=_FAKE_TEAM_ID,
        email="alice@example.com",
        hashed_password="$2b$12$dummy",
        display_name="Alice",
        role="researcher",
    )


class _ScalarsResult:
    """模拟 ``session.scalars(stmt).all()`` 链式调用。"""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _FakeAsyncSession:
    """可配置的 AsyncSession 替身。

    测试通过 ``scalar_fn`` / ``scalars_fn`` 回调控制查询返回值。
    ``add`` 收集对象，``flush`` 为新对象分配 ULID。
    """

    def __init__(self) -> None:
        self._added: list[Any] = []
        self.scalar_fn: Callable[[Any], Any] = lambda _: None
        self.scalars_fn: Callable[[Any], list[Any]] = lambda _: []

    def add(self, obj: Any) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        for obj in self._added:
            if getattr(obj, "id", None) is None:
                obj.id = new_ulid()

    async def commit(self) -> None:
        pass

    async def scalar(self, stmt: Any) -> Any:
        return self.scalar_fn(stmt)

    async def scalars(self, stmt: Any) -> _ScalarsResult:
        return _ScalarsResult(self.scalars_fn(stmt))


def _build_test_app(settings: Settings, session: _FakeAsyncSession) -> FastAPI:
    """构造最小 FastAPI app：挂 projects 路由 + 全局异常处理器 + 依赖覆盖。"""
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(projects_router)

    async def _fake_db_session():
        yield session

    app.dependency_overrides[api_deps.db_session] = _fake_db_session

    @app.exception_handler(AppError)
    async def _app_error_handler(_request, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return app


# ====== Fixtures ======


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def session() -> _FakeAsyncSession:
    return _FakeAsyncSession()


@pytest.fixture
def client(settings: Settings, session: _FakeAsyncSession) -> TestClient:
    """已认证用户的 TestClient。"""
    session.scalar_fn = lambda _: _make_fake_user()
    app = _build_test_app(settings, session)
    return TestClient(app, raise_server_exceptions=True)


def _auth_headers(settings: Settings) -> dict[str, str]:
    token = create_access_token(_FAKE_USER_ID, settings=settings)
    return {"Authorization": f"Bearer {token}"}


# ====== 测试用例 ======


def test_list_projects_without_token_returns_401(client: TestClient) -> None:
    """未认证请求应返回 401。"""
    resp = client.get("/projects")
    assert resp.status_code == 401


def test_list_projects_returns_200(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """合法 token 查询项目列表应返回 200。"""
    now = datetime.now(tz=UTC)
    p1 = Project(
        id=new_ulid(), team_id=_FAKE_TEAM_ID, owner_id=_FAKE_USER_ID,
        name="项目A", description="测试A", default_tier="standard", status="active",
        created_at=now, updated_at=now,
    )
    # 第一次 scalar 返回 user（current_user），后续 scalars 返回项目列表
    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        return None

    session.scalar_fn = _scalar_fn
    session.scalars_fn = lambda _: [p1]

    resp = client.get("/projects", headers=_auth_headers(settings))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "项目A"
    assert body[0]["team_id"] == _FAKE_TEAM_ID


def test_create_project_returns_201(
    settings: Settings, client: TestClient
) -> None:
    """合法 token 创建项目应返回 201。"""
    resp = client.post(
        "/projects",
        json={"name": "新项目", "description": "测试描述", "default_tier": "standard"},
        headers=_auth_headers(settings),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "新项目"
    assert body["team_id"] == _FAKE_TEAM_ID
    assert body["owner_id"] == _FAKE_USER_ID
    assert body["status"] == "active"
    assert body["id"]


def test_create_project_without_token_returns_401(client: TestClient) -> None:
    """未认证创建应返回 401。"""
    resp = client.post("/projects", json={"name": "无主项目"})
    assert resp.status_code == 401
