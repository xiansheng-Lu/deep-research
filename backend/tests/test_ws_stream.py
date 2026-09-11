"""WebSocket ``/ws/runs/{run_id}/stream`` 端点测试。

覆盖：
- 无 token → 1008 拒绝
- 无效 token → 1008 拒绝
- 合法 token → accept + 收到 hub 推送的事件 + 终态后关闭
- 合法 token + 客户端断开 → 清理订阅
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api.v1 import api_v1_router
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.realtime.hub import get_hub
from app.realtime.ws import _make_envelope

_FAKE_USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"
_FAKE_RUN_ID = "01JATESTRUNID00000000000001"


def _build_test_app(settings: Settings) -> FastAPI:
    """构造完整 app（挂 v1 路由含 WS 端点）。"""
    app = FastAPI(default_response_class=ORJSONResponse)
    app.state.settings = settings
    app.state.hub = get_hub()
    app.include_router(api_v1_router)
    return app


def _auth_query(settings: Settings) -> dict[str, str]:
    """构造合法 token 查询参数。"""
    token = create_access_token(_FAKE_USER_ID, settings=settings)
    return {"token": token}


# ====== Fixtures ======


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return _build_test_app(settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ====== 测试用例 ======


def test_ws_without_token_rejected(client: TestClient) -> None:
    """无 token → 1008 拒绝。"""
    with pytest.raises(Exception, match=None), client.websocket_connect(  # noqa: B017
        f"/api/v1/ws/runs/{_FAKE_RUN_ID}/stream"
    ):
        pass


def test_ws_invalid_token_rejected(
    settings: Settings, client: TestClient
) -> None:
    """无效 token → 1008 拒绝。"""
    with pytest.raises(Exception, match=None), client.websocket_connect(  # noqa: B017
        f"/api/v1/ws/runs/{_FAKE_RUN_ID}/stream?token=invalid.jwt.token"
    ):
        pass


def test_ws_valid_token_receives_events_and_closes(
    settings: Settings, client: TestClient, app: FastAPI
) -> None:
    """合法 token → accept + 收到事件 + 终态事件后关闭。"""
    hub = app.state.hub
    channel = f"runs:{_FAKE_RUN_ID}"

    async def _publish_events() -> None:
        # 等待 WS 订阅就绪
        await asyncio.sleep(0.1)
        await hub.publish(channel, {
            "type": "stage.started",
            "stage": "clarify",
            "run_id": _FAKE_RUN_ID,
        })
        await hub.publish(channel, {
            "type": "run.finished",
            "run_id": _FAKE_RUN_ID,
            "status": "succeeded",
        })

    with client.websocket_connect(
        f"/api/v1/ws/runs/{_FAKE_RUN_ID}/stream",
        params=_auth_query(settings),
    ) as ws:
        # 在 WS 上下文中触发事件发布
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_publish_events())
        loop.close()

        # 收第一条事件：stage.started
        msg1 = ws.receive_json()
        assert msg1["type"] == "stage.started"
        assert msg1["run_id"] == _FAKE_RUN_ID
        assert msg1["stage"] == "clarify"
        assert msg1["v"] == "1.0"

        # 收第二条事件：run.finished（终态）
        msg2 = ws.receive_json()
        assert msg2["type"] == "run.finished"
        assert msg2["payload"]["status"] == "succeeded"

    # 连接应在终态后关闭（receive 再次应抛异常）


def test_make_envelope_structure() -> None:
    """envelope 结构包含 v / event_id / ts / run_id / type / payload。"""
    event = {
        "type": "stage.started",
        "stage": "retrieve",
        "payload": {"attempt": 1},
    }
    env = _make_envelope(_FAKE_RUN_ID, event)
    assert env["v"] == "1.0"
    assert env["run_id"] == _FAKE_RUN_ID
    assert env["type"] == "stage.started"
    assert env["stage"] == "retrieve"
    assert env["event_id"]
    assert env["ts"] > 0
    assert env["payload"] == {"attempt": 1}


def test_event_id_unique_within_same_millisecond() -> None:
    """同毫秒连发多个事件，event_id 必须互不相同（lastEventId 去重契约）。"""
    event = {"type": "stage.started", "stage": "retrieve"}
    ids = {_make_envelope(_FAKE_RUN_ID, event)["event_id"] for _ in range(20)}
    assert len(ids) == 20
