"""WebSocket 端点：研究运行阶段事件实时推送。

对齐 LLD §4.3.1；M1 阶段交付：
- ``WS /api/v1/ws/runs/{run_id}/stream``：订阅指定 run 的实时事件流

鉴权：查询参数 ``?token=<jwt>``（M1 简化方案；M2 切到 Sec-WebSocket-Protocol 子协议）
心跳：服务端每 30s 发 ``{"type": "ping"}``，客户端 60s 内须回 ``{"type": "pong"}``，否则断开
终态：收到 ``run.finished`` / ``run.failed`` 事件后推送完毕即关闭连接
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import decode_token
from app.realtime.hub import RealtimeHub, get_hub

log = get_logger("realtime.ws")

router = APIRouter(prefix="/ws/runs", tags=["realtime"])

# 心跳间隔（秒）：服务端每 PING_INTERVAL 秒发一次 ping
_PING_INTERVAL = 30
# pong 超时（秒）：客户端须在 PONG_TIMEOUT 秒内回 pong
_PONG_TIMEOUT = 60

# 终态事件类型：收到后推送完毕即关闭连接
_TERMINAL_TYPES = frozenset({"run.finished", "run.failed"})


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
        "event_id": f"evt_{event_type}_{int(time.time() * 1000)}",
        "ts": int(time.time() * 1000),
        "run_id": run_id,
        "stage": event.get("stage") or event.get("current_stage"),
        "type": event_type,
        "payload": event.get("payload", event),
    }


@router.websocket("/{run_id}/stream")
async def stream_run_events(
    websocket: WebSocket,
    run_id: str,
    token: str = Query(default=""),
) -> None:
    """订阅指定 run 的实时事件流。

    流程：鉴权 → accept → 并行转发事件 + 心跳 + 接收客户端消息；
    收到终态事件（run.finished / run.failed）后关闭连接。
    """
    settings = get_settings()

    # 鉴权：token 缺失或无效直接拒绝
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user_id = _authenticate_ws(token, settings)
    log.info("WS 连接建立", extra={"run_id": run_id, "user_id": user_id})

    await websocket.accept()

    hub: RealtimeHub = get_hub()
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
        """接收客户端消息（pong / intervene / cancel）。

        M1 阶段仅处理 pong 心跳应答；intervene/cancel 走 REST 接口。
        若 PONG_TIMEOUT 秒内无任何消息，视为超时断开。
        """
        while not finished.is_set():
            try:
                msg = await asyncio.wait_for(
                    websocket.receive_json(), timeout=_PONG_TIMEOUT
                )
            except TimeoutError:
                log.warning("WS pong 超时，断开连接", extra={"run_id": run_id})
                finished.set()
                return
            msg_type = str(msg.get("type", ""))
            if msg_type == "pong":
                continue
            # M1 阶段不处理 intervene/cancel（走 REST 接口）

    forward_task = asyncio.create_task(_forward_events())
    heartbeat_task = asyncio.create_task(_heartbeat())
    receive_task = asyncio.create_task(_receive_messages())

    try:
        # 任一任务退出即结束（终态事件 / 客户端断开 / pong 超时）
        done, pending = await asyncio.wait(
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
        with contextlib.suppress(Exception):  # 连接可能已关闭
            await websocket.close()
        log.info("WS 连接关闭", extra={"run_id": run_id, "user_id": user_id})


__all__ = ["router"]
