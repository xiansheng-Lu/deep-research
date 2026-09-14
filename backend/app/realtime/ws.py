"""WebSocket 端点：研究运行阶段事件实时推送。

对齐 LLD §4.3.1 与 M2-5 用户介入方案 §5.5：
- ``WS /api/v1/ws/runs/{run_id}/stream``：订阅指定 run 的实时事件流
- 握手期完成 JWT 鉴权与 run 创建者归属校验（失败一律 1008 拒绝，不泄漏存在性）
- 连接内接收客户端控制指令 ``intervene`` / ``cancel``，与 REST 等价并回
  ``intervene.ack`` / ``intervene.error``（``request_id`` 原样透传）

鉴权：查询参数 ``?token=<jwt>``（M1 简化方案；M2 切到 Sec-WebSocket-Protocol 子协议）
心跳：服务端每 30s 发 ``{"type": "ping"}``，客户端 60s 内须回 ``{"type": "pong"}``，否则断开
终态：收到 ``run.finished`` / ``run.failed`` 事件后推送完毕即关闭连接
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, AuthError, ValidationError
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.models.identity import User
from app.db.models.run import ResearchRun
from app.observability.metrics import ws_active_connections
from app.orchestrator.registry import RunRegistry, get_run_registry
from app.realtime.hub import RealtimeHub, get_hub
from app.schemas.runs import CancelRunRequest, InterventionAction
from app.services import runs_control
from app.services.conflicts import get_owned_run
from app.services.interventions import submit_intervention

log = get_logger("realtime.ws")

router = APIRouter(prefix="/ws/runs", tags=["realtime"])

# 心跳间隔（秒）：服务端每 PING_INTERVAL 秒发一次 ping
_PING_INTERVAL = 30
# pong 超时（秒）：客户端须在 PONG_TIMEOUT 秒内回 pong；期间任何消息都视为存活
_PONG_TIMEOUT = 60

# 终态事件类型：收到后推送完毕即关闭连接
_TERMINAL_TYPES = frozenset({"run.finished", "run.failed"})

# 连接内支持的客户端控制指令
_COMMAND_TYPES = frozenset({"intervene", "cancel"})


def _authenticate_ws(token: str, settings: Settings) -> str:
    """校验 JWT 并返回 user_id；失败抛 WebSocketDisconnect。"""
    try:
        payload = decode_token(token, settings=settings)
    except JWTError:
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION) from None
    if payload.get("type") != "access":
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)
    return user_id


def _make_envelope(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """构造 WS 推送 envelope（对齐 HLD §7.4 外层结构）。"""
    event_type = str(event.get("type", "unknown"))
    return {
        "v": "1.0",
        # 同毫秒可能连发多个阶段事件，时间戳后缀会重复，追加随机段保证
        # event_id 全局唯一（客户端 lastEventId 去重的前置契约）
        "event_id": f"evt_{event_type}_{int(time.time() * 1000)}_{uuid4().hex[:8]}",
        "ts": int(time.time() * 1000),
        "run_id": run_id,
        "stage": event.get("stage") or event.get("current_stage"),
        "type": event_type,
        "payload": event.get("payload", event),
    }


def _make_command_envelope(request_id: str, envelope_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """构造客户端控制指令应答信封（intervene.ack/intervene.error，无 run 维度）。"""
    return {
        "v": "1.0",
        "ts": int(time.time() * 1000),
        "type": envelope_type,
        "request_id": request_id,
        "payload": payload,
    }


async def _verify_run_access(factory: Any, run_id: str, user_id: str) -> bool:
    """握手期归属校验：用户存在且未注销、run 由该用户创建；任何异常按拒绝处理。"""
    try:
        async with factory() as session:
            user = await session.scalar(select(User).where(User.id == user_id))
            if user is None or user.deleted_at is not None:
                return False
            run = await session.scalar(
                select(ResearchRun.id)
                .where(ResearchRun.id == run_id)
                .where(ResearchRun.creator_id == user_id)
            )
            return run is not None
    except Exception as exc:  # noqa: BLE001 - DB 不可用时拒绝连接，REST 同口径失败
        log.warning(
            "WS 握手归属校验异常，按拒绝处理",
            extra={"run_id": run_id, "user_id": user_id, "error": repr(exc)},
        )
        return False


def _business_error_code(exc: AppError) -> str:
    """提取业务判别码：ConflictError 等的具体码放 details.code，缺省回退异常码。"""
    code = exc.details.get("code")
    return str(code) if code else exc.code


async def _send_command_ack(websocket: WebSocket, request_id: str, status_value: str) -> None:
    """回 intervene.ack；连接已关闭时静默（与事件推送同口径）。"""
    with contextlib.suppress(Exception):  # noqa: BLE001 - 应答发送失败不扩散
        await websocket.send_json(
            _make_command_envelope(request_id, "intervene.ack", {"status": status_value})
        )


async def _send_command_error(websocket: WebSocket, request_id: str, code: str, message: str) -> None:
    """回 intervene.error；连接已关闭时静默。"""
    with contextlib.suppress(Exception):  # noqa: BLE001 - 应答发送失败不扩散
        await websocket.send_json(
            _make_command_envelope(request_id, "intervene.error", {"code": code, "message": message})
        )


async def _handle_client_command(
    websocket: WebSocket,
    *,
    factory: Any,
    registry: RunRegistry,
    hub: RealtimeHub,
    run_id: str,
    user_id: str,
    msg: dict[str, Any],
) -> None:
    """处理单条 intervene/cancel 指令：复用 REST 同一 service，回 ack/error。"""
    request_id = msg.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        # 无 request_id 无法关联应答，丢弃（前端 sendCommand 必定生成）
        log.warning("WS 控制指令缺少 request_id，已忽略", extra={"run_id": run_id})
        return

    command_type = str(msg.get("type", ""))
    if command_type not in _COMMAND_TYPES:
        await _send_command_error(websocket, request_id, "UNKNOWN_COMMAND", f"未知指令类型：{command_type}")
        return

    raw_payload = msg.get("payload")
    if not isinstance(raw_payload, dict):
        raw_payload = {}

    async with factory() as session:
        try:
            # 归属：与 REST get_owned_run 同口径（连接建立后 run 可能已被转交/删除）
            run = await get_owned_run(session, run_id, user_id)
            user = await session.scalar(select(User).where(User.id == user_id))
            if user is None or user.deleted_at is not None:
                raise AuthError("用户不存在或已注销")

            if command_type == "intervene":
                try:
                    action = InterventionAction.model_validate(raw_payload)
                except PydanticValidationError as exc:
                    raise ValidationError(
                        "介入指令载荷格式非法",
                        details={"code": "INVALID_ACTION_PAYLOAD"},
                    ) from exc
                run = await submit_intervention(
                    session,
                    run=run,
                    user_id=user.id,
                    team_id=user.team_id,
                    action=action,
                    idempotency_key=None,
                )
            else:
                try:
                    body = CancelRunRequest.model_validate(raw_payload)
                except PydanticValidationError as exc:
                    raise ValidationError(
                        "取消指令载荷格式非法",
                        details={"code": "INVALID_ACTION_PAYLOAD"},
                    ) from exc
                run, _partial_report_id = await runs_control.cancel_run(
                    session,
                    run=run,
                    registry=registry,
                    hub=hub,
                    team_id=user.team_id,
                    user_id=user.id,
                    keep_partial=body.keep_partial,
                    reason=body.reason,
                )
            final_status = str(run.status)
        except AppError as exc:
            await session.rollback()
            log.info(
                "WS 控制指令被业务拒绝",
                extra={
                    "run_id": run_id,
                    "command": command_type,
                    "code": _business_error_code(exc),
                },
            )
            await _send_command_error(websocket, request_id, _business_error_code(exc), exc.message)
            return
        except Exception as exc:  # noqa: BLE001 - 指令异常必须回 error 帧，不杀连接
            await session.rollback()
            log.warning(
                "WS 控制指令处理异常",
                extra={"run_id": run_id, "command": command_type, "error": repr(exc)},
            )
            await _send_command_error(websocket, request_id, "internal_error", "控制指令处理失败，请稍后重试")
            return

    await _send_command_ack(websocket, request_id, final_status)


@router.websocket("/{run_id}/stream")
async def stream_run_events(
    websocket: WebSocket,
    run_id: str,
    token: str = Query(default=""),
) -> None:
    """订阅指定 run 的实时事件流。

    流程：鉴权 + 归属校验 → accept → 并行转发事件 + 心跳 + 接收客户端消息；
    收到终态事件（run.finished / run.failed）后关闭连接。
    """
    settings = get_settings()

    # 鉴权：token 缺失或无效直接拒绝
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user_id = _authenticate_ws(token, settings)

    # 归属校验：lifespan 保证会话工厂已注入；未初始化/校验失败一律 1008 拒绝
    factory = getattr(websocket.app.state, "session_factory", None)
    if factory is None or not await _verify_run_access(factory, run_id, user_id):
        log.info(
            "WS 握手被拒（资源不存在/非创建者/工厂未就绪）",
            extra={"run_id": run_id, "user_id": user_id},
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    log.info("WS 连接建立", extra={"run_id": run_id, "user_id": user_id})

    await websocket.accept()
    ws_active_connections.inc()

    hub: RealtimeHub = getattr(websocket.app.state, "hub", None) or get_hub()
    registry = get_run_registry()
    channel = f"runs:{run_id}"

    # 终态信号：转发任务检测到终态后置 True，通知心跳/接收任务退出
    finished = asyncio.Event()

    async def _forward_events() -> None:
        """从 hub 订阅事件，封装 envelope 后推送给客户端。"""
        async for event in hub.subscribe(channel):
            envelope = _make_envelope(run_id, event)
            await websocket.send_json(envelope)
            if str(event.get("type")) in _TERMINAL_TYPES:
                finished.set()
                break

    async def _heartbeat() -> None:
        """定时发送 ping；收到终态信号后退出。"""
        while not finished.is_set():
            try:
                await asyncio.wait_for(finished.wait(), timeout=_PING_INTERVAL)
            except TimeoutError:
                await websocket.send_json({"v": "1.0", "type": "ping"})

    async def _receive_messages() -> None:
        """接收客户端消息（pong 心跳 / intervene / cancel 控制指令）。

        - 控制指令复用 REST 同一 service，应答带 request_id 供前端关联；
        - 指令处理异常回 error 帧，不断开连接；
        - PONG_TIMEOUT 秒内无任何消息，视为超时断开。
        """
        while not finished.is_set():
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=_PONG_TIMEOUT)
            except TimeoutError:
                log.warning("WS pong 超时，断开连接", extra={"run_id": run_id})
                finished.set()
                return
            except WebSocketDisconnect:
                finished.set()
                return
            except Exception as exc:  # noqa: BLE001 - 非法 JSON 等：丢弃该条保活
                log.warning(
                    "WS 收到无法解析的消息，已忽略",
                    extra={"run_id": run_id, "error": repr(exc)},
                )
                continue

            msg_type = str(msg.get("type", ""))
            if msg_type == "pong":
                continue
            if not isinstance(msg, dict):
                log.debug("WS 收到非对象消息，已忽略", extra={"type": msg_type})
                continue
            # 已知/未知指令统一进处理器：非 intervene/cancel 由其回 UNKNOWN_COMMAND
            await _handle_client_command(
                websocket,
                factory=factory,
                registry=registry,
                hub=hub,
                run_id=run_id,
                user_id=user_id,
                msg=msg,
            )

    forward_task = asyncio.create_task(_forward_events())
    heartbeat_task = asyncio.create_task(_heartbeat())
    receive_task = asyncio.create_task(_receive_messages())

    try:
        # 任一任务退出即结束（终态事件 / 客户端断开 / pong 超时）
        await asyncio.wait(
            {forward_task, heartbeat_task, receive_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except WebSocketDisconnect:
        log.info("WS 客户端断开", extra={"run_id": run_id, "user_id": user_id})
    finally:
        finished.set()
        for task in (forward_task, heartbeat_task, receive_task):
            task.cancel()
        # 等待取消完成，避免协程泄漏
        await asyncio.gather(forward_task, heartbeat_task, receive_task, return_exceptions=True)
        ws_active_connections.dec()
        with contextlib.suppress(Exception):  # 连接可能已关闭
            await websocket.close()
        log.info("WS 连接关闭", extra={"run_id": run_id, "user_id": user_id})


__all__ = ["router"]
