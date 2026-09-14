"""在途研究任务注册表（M2-5）。

研究任务由 API 进程内 ``asyncio.create_task`` 调度，进程内需要一张 run_id 到
协程句柄的映射，才能让 pause/cancel 控制端点寻址到在途任务。LangGraph 没有
运行中 pause 原语，本注册表配合协作式取消（对任务发 ``task.cancel()``，执行器
在 super-step 边界捕获 ``CancelledError`` 后按 mode 落库收尾）实现软暂停/硬取消。

单进程约束：跨进程控制（多 API worker / Celery 接管）在 M2-8 改为 DB 租约 +
消息信号，本模块届时替换实现而控制端点签名不变。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from app.core.logging import get_logger

log = get_logger("orchestrator.registry")

#: 受控停止模式：pause=软暂停（可恢复），cancel=硬取消（终态）
ControlMode = Literal["pause", "cancel"]

#: 模式强度序：cancel 强于 pause，并发信号只允许升级不允许降级
_MODE_RANK: dict[str, int] = {"pause": 1, "cancel": 2}


@dataclass(slots=True)
class RunHandle:
    """单个在途 run 的协程句柄与停止信号。"""

    task: asyncio.Task[Any]
    mode: ControlMode | None = None
    # cancel 时是否保留报告草稿（契约草案 §6.1 keep_partial，默认 true）
    keep_partial: bool = True


class RunRegistry:
    """进程内 run_id → 在途协程句柄注册表。"""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}

    def register(self, run_id: str, task: asyncio.Task[Any]) -> None:
        """登记在途任务；重复登记同一 run 以新句柄覆盖（理论上不应发生）。"""
        if run_id in self._handles:
            log.warning("运行任务重复登记，以新句柄覆盖", extra={"run_id": run_id})
        self._handles[run_id] = RunHandle(task=task)

    def unregister(self, run_id: str) -> None:
        """任务协程退出时移除句柄；句柄不存在时静默。"""
        self._handles.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        """是否存在仍在运行（未 done）的协程句柄。"""
        handle = self._handles.get(run_id)
        return handle is not None and not handle.task.done()

    def request_stop(
        self,
        run_id: str,
        mode: ControlMode,
        *,
        keep_partial: bool = True,
    ) -> bool:
        """对在途任务投递受控停止信号。

        - 句柄不存在或任务已结束：返回 False（调用方按"无协程可取消"自行收尾）；
        - 已有信号时只允许升级（pause→cancel），不允许降级；
        - 投递 ``task.cancel()``，执行器在下一 await 点收到 CancelledError。
        """
        handle = self._handles.get(run_id)
        if handle is None or handle.task.done():
            return False
        if handle.mode is not None:
            # 信号已投递、CancelledError 尚未被采样的窗口内到达第二个信号：
            # 只允许 pause→cancel 升级（更新 mode 即可，协程读取时拿到最新值，
            # 重复 cancel() 不会产生第二次 CancelledError）；降级忽略。
            if _MODE_RANK.get(mode, 0) > _MODE_RANK.get(handle.mode, 0):
                handle.mode = mode
                handle.keep_partial = keep_partial
            return True
        handle.mode = mode
        handle.keep_partial = keep_partial
        handle.task.cancel()
        return True

    def consume_mode(self, run_id: str) -> ControlMode | None:
        """执行器捕获 CancelledError 后读取停止模式（不清除，供兜底重查）。"""
        handle = self._handles.get(run_id)
        return handle.mode if handle is not None else None

    def keep_partial_for(self, run_id: str) -> bool:
        """读取该 run 取消信号携带的 keep_partial 参数。"""
        handle = self._handles.get(run_id)
        return handle.keep_partial if handle is not None else True

    def clear(self) -> None:
        """清空注册表（仅供测试隔离使用）。"""
        self._handles.clear()


#: 进程级单例（与 RealtimeHub 的 get_hub 模式一致）
_registry = RunRegistry()


def get_run_registry() -> RunRegistry:
    """返回进程内运行任务注册表单例。"""
    return _registry


__all__ = [
    "ControlMode",
    "RunHandle",
    "RunRegistry",
    "get_run_registry",
]
