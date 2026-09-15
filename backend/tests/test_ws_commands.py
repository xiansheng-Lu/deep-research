"""M2-5 WS 客户端控制指令测试（AC-11）。

覆盖：
- 握手归属校验：非 run 创建者的合法 token 一律 1008 拒绝；
- intervene 指令成功回 intervene.ack，request_id 原样透传；
- intervene 阶段/载荷非法回 intervene.error（INTERVENE_NOT_ALLOWED /
  INVALID_ACTION_PAYLOAD）；
- cancel 指令成功回 ack 且 run 落 cancelled（在途假任务收到取消信号，不发终态帧）；
- 未知指令回 UNKNOWN_COMMAND；缺少 request_id 的消息被忽略不影响后续指令。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient
from test_runs_control_api import (
    RUN_ID,
    TEAM_ID,
    USER_ID,
    _ControlFakeSession,
    _FakeTask,
    _register_sync,
    _run,
    _subq,
    _user,
)
from test_ws_stream import _SessionFactory

from app.api.v1 import api_v1_router
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.orchestrator.registry import get_run_registry
from app.realtime.hub import RealtimeHub

_OTHER_USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT9"


def _other_user() -> Any:
    from app.db.models.identity import User

    return User(
        id=_OTHER_USER_ID,
        team_id=TEAM_ID,
        email="bob@example.com",
        hashed_password="$2b$dummy",
        display_name="Bob",
        role="researcher",
    )


def _client(
    *,
    token_user_id: str = USER_ID,
    run_status: str = "running",
    current_stage: str | None = "retrieve",
    extra_users: list[Any] | None = None,
    sub_questions: list[Any] | None = None,
) -> tuple[TestClient, _ControlFakeSession, Any]:
    """构造带 WS 路由与内存会话的客户端；返回 (client, 内存会话, run 行)。"""
    run = _run(run_status)
    run.current_stage = current_stage
    session = _ControlFakeSession(
        [_user(), *(extra_users or [])],
        [run],
        sub_questions=sub_questions or [],
    )
    app = FastAPI(default_response_class=ORJSONResponse)
    app.state.hub = RealtimeHub()
    app.state.session_factory = _SessionFactory(session)
    app.include_router(api_v1_router)
    return TestClient(app), session, run


def _ws_token(user_id: str) -> str:
    settings: Settings = get_settings()
    return create_access_token(user_id, settings=settings)


@pytest.fixture
def _clean_registry() -> Any:
    """隔离进程级运行任务注册表单例。"""
    registry = get_run_registry()
    registry.clear()
    yield registry
    registry.clear()


def _connect(client: TestClient, user_id: str = USER_ID):
    return client.websocket_connect(f"/api/v1/ws/runs/{RUN_ID}/stream?token={_ws_token(user_id)}")


def _command(command_type: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"v": "1.0", "type": command_type, "request_id": request_id, "payload": payload}


def test_ws_handshake_rejects_non_owner() -> None:
    """AC-11：合法 token 但非 run 创建者 → 握手 1008 拒绝。"""
    client, _, _ = _client(extra_users=[_other_user()])
    with pytest.raises(Exception, match=None), _connect(client, _OTHER_USER_ID):  # noqa: B017
        pass


def test_ws_intervene_command_returns_ack(_clean_registry: Any) -> None:
    """AC-11：intervene ask_followup 受理 → intervene.ack，request_id 透传。"""
    client, session, _ = _client(sub_questions=[_subq()])
    with _connect(client) as ws:
        ws.send_json(
            _command(
                "intervene",
                "req-001",
                {
                    "type": "ask_followup",
                    "payload": {"sub_question_id": "sq-1", "question": "补充追问近三年数据"},
                },
            )
        )
        ack = ws.receive_json()

    assert ack["type"] == "intervene.ack"
    assert ack["request_id"] == "req-001"
    assert ack["v"] == "1.0"
    assert ack["payload"] == {"status": "running"}
    assert len(session.interventions) == 1
    assert session.interventions[0].type == "ask_followup"
    assert session.interventions[0].status == "pending"


def test_ws_intervene_wrong_stage_returns_error(_clean_registry: Any) -> None:
    """阶段不允许 → intervene.error，业务判别码 INTERVENE_NOT_ALLOWED。"""
    client, session, _ = _client(current_stage="decompose", sub_questions=[_subq()])
    with _connect(client) as ws:
        ws.send_json(
            _command(
                "intervene",
                "req-002",
                {"type": "ask_followup", "payload": {"sub_question_id": "sq-1", "question": "x"}},
            )
        )
        reply = ws.receive_json()

    assert reply["type"] == "intervene.error"
    assert reply["request_id"] == "req-002"
    assert reply["payload"]["code"] == "INTERVENE_NOT_ALLOWED"
    assert reply["payload"]["message"]
    assert session.interventions == []


def test_ws_intervene_invalid_payload_returns_error(_clean_registry: Any) -> None:
    """载荷结构非法（缺 type）→ intervene.error，码 INVALID_ACTION_PAYLOAD。"""
    client, _, _ = _client()
    with _connect(client) as ws:
        ws.send_json(_command("intervene", "req-003", {}))
        reply = ws.receive_json()

    assert reply["type"] == "intervene.error"
    assert reply["request_id"] == "req-003"
    assert reply["payload"]["code"] == "INVALID_ACTION_PAYLOAD"


def test_ws_cancel_command_returns_ack(_clean_registry: Any) -> None:
    """AC-11：cancel 指令 → ack cancelled，在途任务收到取消信号。"""
    client, _, run = _client()
    fake_task = _FakeTask()
    _register_sync(_clean_registry, RUN_ID, fake_task)
    with _connect(client) as ws:
        ws.send_json(_command("cancel", "req-004", {"reason": "user_cancel"}))
        ack = ws.receive_json()

    assert ack["type"] == "intervene.ack"
    assert ack["request_id"] == "req-004"
    assert ack["payload"]["status"] == "cancelled"
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert fake_task.cancel_calls == 1


def test_ws_unknown_command_returns_error(_clean_registry: Any) -> None:
    """WS 仅支持 intervene/cancel（pause 走 REST）；未知指令回 UNKNOWN_COMMAND。"""
    client, _, _ = _client()
    with _connect(client) as ws:
        ws.send_json(_command("pause", "req-005", {}))
        reply = ws.receive_json()

    assert reply["type"] == "intervene.error"
    assert reply["request_id"] == "req-005"
    assert reply["payload"]["code"] == "UNKNOWN_COMMAND"


def test_ws_command_without_request_id_ignored(_clean_registry: Any) -> None:
    """缺 request_id 的指令被忽略；后续合法指令仍正常回 ack。"""
    client, session, _ = _client(sub_questions=[_subq()])
    with _connect(client) as ws:
        ws.send_json({"v": "1.0", "type": "intervene", "payload": {}})
        ws.send_json(
            _command(
                "intervene",
                "req-006",
                {"type": "ask_followup", "payload": {"sub_question_id": "sq-1", "question": "补充问题"}},
            )
        )
        ack = ws.receive_json()

    assert ack["type"] == "intervene.ack"
    assert ack["request_id"] == "req-006"
    assert len(session.interventions) == 1
