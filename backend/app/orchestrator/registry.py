"""在途研究任务注册表（M2-5）：进程内 run_id → 协程句柄注册表。

LangGraph 无运行中 pause 原语，本注册表持有在途 asyncio 任务，以注册表信号
配合 ``task.cancel()`` 实现软暂停/硬取消：

- ``pause``：服务层校验 running 且注册表有句柄后，乐观置 paused 并提交，
  再 ``task.cancel()`` 投递协作式取消信号，协程在下一 await 点落 paused；
- ``cancel``：服务层可直接置 cancelled 并提交（paused 孤儿由服务端直接终态，
  无协程可信号），有协程句柄时同步发取消信号，协程落 cancelled。

单进程约束：研究任务仍在 API 进程内 ``create_task``，注册表为进程内单例；
跨进程恢复/多 worker、注册租约 + 消息信号在 M2-8 替换。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from app.core.logging import get_logger

log = get_logger("orchestrator.registry")

#: 受控停止模式：pause=软暂停（可恢复），cancel=硬取消（终态）
ControlMode = Literal["pause", "cancel"]


@dataclass(slots=True)
class RunHandle:
    """单个在途 run 的协程句柄与停止信号。

    Attributes:
        task: 驱动 run 的 asyncio 任务；``None`` 表示恢复服务已抢占但恢复
            协程尚未注册的预留占位（reservation）。
        mode: 停止模式；None 表示未收到停止信号。
        keep_partial: cancel 时是否保留报告草稿（M2 keep_partial，默认 true）。
    """

    task: asyncio.Task[Any] | None
    mode: ControlMode | None = None
    # 取消时是否保留报告草稿（cancel keep_partial，默认 true）
    keep_partial: bool = True


class RunRegistry:
    """进程内 run_id → 在途协程句柄注册表。"""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}

    def register(self, run_id: str, task: asyncio.Task[Any]) -> RunHandle | None:
        """登记在途任务句柄，返回被取代的旧句柄（无则 None）。

        恢复协程接管预留占位（旧句柄 ``task is None``）时沿用占位上已到达的
        停止信号（pause/cancel 可能落在服务预翻转与协程注册之间的微窗口）；
        取代真实在途任务时不沿用旧信号（属于正常的暂停→恢复交接，旧任务
        随后自行放弃收尾，由调用方等待其退出）。
        """
        prior = self._handles.get(run_id)
        if prior is not None and prior.task is None:
            # 接管预留占位：携带其上的停止信号与草稿参数
            new_handle = RunHandle(
                task=task,
                mode=prior.mode,
                keep_partial=prior.keep_partial,
            )
            self._handles[run_id] = new_handle
            # 信号落在占位上时没有任务可 cancel；接管后必须对真实任务补发，
            # 否则 pause/cancel 信号只停留在标记上，协程永不退出
            if new_handle.mode is not None:
                task.cancel()
        else:
            self._handles[run_id] = RunHandle(task=task)
        return prior

    def reserve(self, run_id: str) -> RunHandle | None:
        """恢复服务在调度协程前同步抢占 run，返回既有句柄（无则 None）。

        占位写入后，pause/cancel 信号可落在占位上（恢复协程注册时沿用）；
        被暂停首跑协程的旧句柄作为返回值交给恢复协程等待退出，避免两个驱动
        循环并发。调用方负责在抢占失败时不重复调度。
        """
        prior = self._handles.get(run_id)
        self._handles[run_id] = RunHandle(task=None)
        return prior

    def restore(self, run_id: str, handle: RunHandle | None) -> None:
        """恢复抢占失败时把旧句柄放回；旧句柄为 None 时移除占位。"""
        if handle is None:
            self._handles.pop(run_id, None)
        else:
            self._handles[run_id] = handle

    def is_reserved(self, run_id: str) -> bool:
        """run 是否存在恢复预留占位（恢复协程尚未注册）。"""
        handle = self._handles.get(run_id)
        return handle is not None and handle.task is None

    def unregister(
        self,
        run_id: str,
        *,
        owner: asyncio.Task[Any] | None = None,
    ) -> None:
        """移除句柄；给定 owner 时仅当句柄属于该任务才移除（属主感知）。

        被恢复流程取代的旧首跑协程结束时不能弹出新恢复句柄；无 owner 调用
        保持无条件弹出语义（仅用于测试/明确清理）。
        """
        handle = self._handles.get(run_id)
        if handle is None:
            return
        if owner is not None and handle.task is not owner:
            log.debug(
                "句柄已被新任务接管，忽略旧任务注销",
                extra={"run_id": run_id},
            )
            return
        self._handles.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        """是否存在仍在运行（未 done）的协程或恢复预留占位。"""
        handle = self._handles.get(run_id)
        if handle is None:
            return False
        if handle.task is None:
            return True
        return not handle.task.done()

    def request_stop(
        self,
        run_id: str,
        mode: ControlMode,
        *,
        keep_partial: bool = True,
    ) -> bool:
        """对在途 run 投递受控停止信号。

        - 句柄是真实任务：置停止模式并 ``task.cancel()``；
        - 句柄是恢复预留占位：只记录信号（无任务可取消），恢复协程注册时
          沿用并按该信号收尾；
        - 已有信号时允许 pause→cancel 升级，不允许降级；
        - 无句柄返回 False（调用方按"无协程可取消"自行收尾）。
        """
        handle = self._handles.get(run_id)
        if handle is None:
            return False
        # 已结束的真实任务无法再收取消信号（占位 task=None 例外：信号待恢复
        # 协程注册时沿用），交由调用方按「无协程可取消」直接服务端收尾
        if handle.task is not None and handle.task.done():
            return False
        if handle.mode is not None:
            if mode == "cancel":
                handle.mode = "cancel"
                handle.keep_partial = keep_partial
            return True
        handle.mode = mode
        handle.keep_partial = keep_partial
        if handle.task is not None:
            handle.task.cancel()
        return True

    def consume_mode(self, run_id: str) -> ControlMode | None:
        """读取并清除停止模式（不清除字典，供兜底重查）。"""
        handle = self._handles.get(run_id)
        if handle is None:
            return None
        mode = handle.mode
        return mode

    def peek_mode(self, run_id: str) -> ControlMode | None:
        """非破坏式读取停止模式（恢复协程判断占位上是否已有信号）。"""
        handle = self._handles.get(run_id)
        return handle.mode if handle is not None else None

    def keep_partial_for(self, run_id: str) -> bool:
        """读取该 run 取消信号携带的 keep_partial 参数。"""
        handle = self._handles.get(run_id)
        return handle.keep_partial if handle is not None else True

    def is_owner(self, run_id: str, task: asyncio.Task[Any]) -> bool:
        """当前句柄是否属于给定任务（被取代的旧协程据此放弃收尾）。"""
        handle = self._handles.get(run_id)
        return handle is not None and handle.task is task

    def clear(self) -> None:
        """清空注册表（仅供测试隔离使用）。"""
        self._handles.clear()


#: 进程级单例（与 RealtimeHub 的 get_hub 模式一致）
_registry = RunRegistry()


def get_run_registry() -> RunRegistry:
    """返回进程内运行任务注册表。"""
    return _registry
