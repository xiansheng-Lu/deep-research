"""在途研究任务注册表（M2-5 / M2-8b）：双态信号注册表。

LangGraph 无运行中 pause 原语，pause/cancel 依赖「停止信号 + 协作收尾」。
M2-8b 起同一套方法签名有两种后端：

- **内存态**（``WORKER_ENABLE_REDIS=false``，单进程/离线测试）：
  ``RunRegistry`` 持有 run_id -> 协程句柄，``pause`` 经注册表置信号并
  ``task.cancel()``，协程在下一 await 点落 paused；``cancel`` 同理。
- **Redis 态**（API 与 worker 分进程）：``RedisRunRegistry`` 以
  ``lease:run:{id}`` 租约（SET NX + 心跳 TTL）承接在途互斥与 liveness，
  以 ``control:run:{id}`` 控制键承接 pause/cancel 信号（只升不降）；
  恢复占位（reserve/restore）被「DB 条件翻转 + 租约 NX」取代，在 Redis
  态为空操作。键语义见 :mod:`app.orchestrator.lease` 与阶段方案 §16。

两套实现的公开方法全部为 async（调用方均在异步上下文），按工厂单例注入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.orchestrator.lease import RunLease

log = get_logger("orchestrator.registry")

#: 受控停止模式：pause=软暂停（可恢复），cancel=硬取消（终态）
ControlMode = Literal["pause", "cancel"]

#: 注册表方法的 owner 入参：内存态为 asyncio 任务，Redis 态为任务标识字符串
RegistryOwner = Any


@runtime_checkable
class RunRegistryLike(Protocol):
    """双态注册表的结构化契约（内存态/Redis 态共同满足）。"""

    is_redis_backed: bool

    async def register(self, run_id: str, owner: RegistryOwner) -> RunHandle | None: ...
    async def reserve(self, run_id: str) -> RunHandle | None: ...
    async def restore(self, run_id: str, handle: RunHandle | None) -> None: ...
    async def is_reserved(self, run_id: str) -> bool: ...
    async def unregister(self, run_id: str, *, owner: RegistryOwner = None) -> None: ...
    async def is_active(self, run_id: str) -> bool: ...
    async def request_stop(self, run_id: str, mode: ControlMode, *, keep_partial: bool = True) -> bool: ...
    async def consume_mode(self, run_id: str) -> ControlMode | None: ...
    async def peek_mode(self, run_id: str) -> ControlMode | None: ...
    async def keep_partial_for(self, run_id: str) -> bool: ...
    async def is_owner(self, run_id: str, owner: RegistryOwner) -> bool: ...
    async def clear_stop_signal(self, run_id: str) -> None: ...


@dataclass(slots=True)
class RunHandle:
    """单个在途 run 的协程句柄与停止信号（仅内存态使用）。

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
    """进程内 run_id → 在途协程句柄注册表（内存态）。"""

    #: 双态标记：内存态
    is_redis_backed: bool = False

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}

    async def register(self, run_id: str, task: asyncio.Task[Any]) -> RunHandle | None:
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

    async def reserve(self, run_id: str) -> RunHandle | None:
        """恢复服务在调度协程前同步抢占 run，返回既有句柄（无则 None）。

        占位写入后，pause/cancel 信号可落在占位上（恢复协程注册时沿用）；
        被暂停首跑协程的旧句柄作为返回值交给恢复协程等待退出，避免两个驱动
        循环并发。调用方负责在抢占失败时不重复调度。
        """
        prior = self._handles.get(run_id)
        self._handles[run_id] = RunHandle(task=None)
        return prior

    async def restore(self, run_id: str, handle: RunHandle | None) -> None:
        """恢复抢占失败时把旧句柄放回；旧句柄为 None 时移除占位。"""
        if handle is None:
            self._handles.pop(run_id, None)
        else:
            self._handles[run_id] = handle

    async def is_reserved(self, run_id: str) -> bool:
        """run 是否存在恢复预留占位（恢复协程尚未注册）。"""
        handle = self._handles.get(run_id)
        return handle is not None and handle.task is None

    async def unregister(
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

    async def is_active(self, run_id: str) -> bool:
        """是否存在仍在运行（未 done）的协程或恢复预留占位。"""
        handle = self._handles.get(run_id)
        if handle is None:
            return False
        if handle.task is None:
            return True
        return not handle.task.done()

    async def request_stop(
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

    async def consume_mode(self, run_id: str) -> ControlMode | None:
        """读取并清除停止模式（不清除字典，供兜底重查）。"""
        handle = self._handles.get(run_id)
        if handle is None:
            return None
        return handle.mode

    async def peek_mode(self, run_id: str) -> ControlMode | None:
        """非破坏式读取停止模式（恢复协程判断占位上是否已有信号）。"""
        handle = self._handles.get(run_id)
        return handle.mode if handle is not None else None

    async def keep_partial_for(self, run_id: str) -> bool:
        """读取该 run 取消信号携带的 keep_partial 参数。"""
        handle = self._handles.get(run_id)
        return handle.keep_partial if handle is not None else True

    async def is_owner(self, run_id: str, task: asyncio.Task[Any]) -> bool:
        """当前句柄是否属于给定任务（被取代的旧协程据此放弃收尾）。"""
        handle = self._handles.get(run_id)
        return handle is not None and handle.task is task

    async def clear_stop_signal(self, run_id: str) -> None:
        """终态后清除停止信号：内存态信号挂在句柄上，unregister 即移除，无需处理。"""
        return None

    def clear(self) -> None:
        """清空注册表（仅供测试隔离使用）。"""
        self._handles.clear()


class RedisRunRegistry:
    """跨进程注册表（Redis 态）：在途活性/互斥走租约，停止信号走控制键。

    方法签名与 :class:`RunRegistry` 对齐，差异语义：

    - ``register``：owner 为任务标识字符串（celery task_id），NX 抢约失败
      返回 ``RunHandle(task=None)`` 标记「已有执行者」，调用方据此安全退出
      （任务幂等，阶段方案 §16.4）；
    - ``reserve/restore/is_reserved``：进程内占位退役（DB 条件翻转 +
      租约 NX 承接恢复双击防护），为空操作；
    - ``is_active``：租约键存在且未过期即视为在途（pause 门槛）；
    - ``request_stop``：无新鲜租约返回 False（调用方按无执行者直接收尾）；
    - ``unregister``：必须带 owner（无主删除可能误清新执行者的租约）。
    """

    def __init__(self, lease: RunLease) -> None:
        self._lease = lease

    #: 双态标记：Redis 态
    is_redis_backed: bool = True

    async def register(self, run_id: str, owner: RegistryOwner) -> RunHandle | None:
        task_id = str(owner)
        if await self._lease.try_acquire(run_id, task_id):
            return None
        # 已有执行者（重投递/重复任务）：无任务句柄作为「他人持有」标记
        return RunHandle(task=None)

    async def reserve(self, run_id: str) -> RunHandle | None:
        return None

    async def restore(self, run_id: str, handle: RunHandle | None) -> None:
        return None

    async def is_reserved(self, run_id: str) -> bool:
        return False

    async def unregister(
        self,
        run_id: str,
        *,
        owner: RegistryOwner = None,
    ) -> None:
        if owner is None:
            log.warning("Redis 态无 owner 的 unregister 被忽略", extra={"run_id": run_id})
            return
        await self._lease.release(run_id, str(owner))

    async def is_active(self, run_id: str) -> bool:
        return await self._lease.holder(run_id) is not None

    async def request_stop(
        self,
        run_id: str,
        mode: ControlMode,
        *,
        keep_partial: bool = True,
    ) -> bool:
        # 无新鲜租约：没有执行者会响应信号，交由调用方按孤儿/无协程路径收尾
        if await self._lease.holder(run_id) is None:
            return False
        return await self._lease.set_control(run_id, mode, keep_partial=keep_partial)

    async def consume_mode(self, run_id: str) -> ControlMode | None:
        signal = await self._lease.read_control(run_id)
        return signal.mode if signal is not None else None

    async def peek_mode(self, run_id: str) -> ControlMode | None:
        signal = await self._lease.read_control(run_id)
        return signal.mode if signal is not None else None

    async def keep_partial_for(self, run_id: str) -> bool:
        signal = await self._lease.read_control(run_id)
        return signal.keep_partial if signal is not None else True

    async def is_owner(self, run_id: str, owner: RegistryOwner) -> bool:
        holder = await self._lease.holder(run_id)
        return (
            holder is not None and holder.worker_id == self._lease.worker_id and holder.task_id == str(owner)
        )

    async def clear_stop_signal(self, run_id: str) -> None:
        """终态后删除 control:run:{id}（终态帧发布后调用，防键泄漏不等 1h TTL）。"""
        await self._lease.clear_control(run_id)

    def clear(self) -> None:
        """Redis 态无进程内状态；测试隔离由 fakeredis 实例重建保证。"""


#: 进程级单例（与 RealtimeHub 的 get_hub/set_default_hub 模式一致）。
#: 未显式注入时为内存态；lifespan/worker bootstrap 按配置注入双态实现。
_registry: RunRegistry | RedisRunRegistry | None = None
_default_memory_registry = RunRegistry()


def get_run_registry() -> RunRegistry | RedisRunRegistry:
    """返回当前进程的运行任务注册表（缺省内存态单例）。"""
    return _registry if _registry is not None else _default_memory_registry


def set_run_registry(registry: RunRegistry | RedisRunRegistry) -> None:
    """注入进程级注册表（lifespan/worker bootstrap 按 WORKER_ENABLE_REDIS 装配）。"""
    global _registry
    _registry = registry


def reset_run_registry() -> None:
    """恢复缺省内存态单例（仅供测试隔离使用）。"""
    global _registry
    _registry = None
    _default_memory_registry.clear()
