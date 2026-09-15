"""在途租约与控制信号的 Redis 键封装（M2-8b）。

权威互斥与停止信号都在 Redis（易失，PG 仅放观测镜像），两类键：

- ``lease:run:{run_id}``：值为 JSON ``{worker_id, task_id, started_at}``，
  带 TTL（默认 30s）。worker 驱动 run 前 ``SET NX PX`` 抢占，驱动期间心跳
  续期，正常结束时按 owner 校验原子 DEL；worker 崩溃则 TTL 过期，孤儿清扫
  据此识别（见阶段方案 §16.2）。
- ``control:run:{run_id}``：值为 JSON ``{mode, keep_partial, updated_at}``，
  ``pause``/``cancel`` 信号只允许升级不允许降级，带 1h 兜底 TTL 防键泄漏
  （见阶段方案 §16.3）。

owner 校验的原子操作（续期/释放）不使用 Lua（fakeredis 默认无 lupa），
改用 Redis WATCH/MULTI 事务，真 Redis 与 fakeredis 行为一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.core.logging import get_logger

log = get_logger("orchestrator.lease")

#: 受控停止模式：pause=软暂停（可恢复），cancel=硬取消（终态）
ControlMode = Literal["pause", "cancel"]

#: 控制信号升级序：数值大者生效（pause -> cancel 允许，反向拒绝）
_MODE_RANK: dict[str, int] = {"pause": 1, "cancel": 2}

#: 控制键兜底 TTL（秒），防终态清理遗漏造成键泄漏
_CONTROL_TTL_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class LeaseHolder:
    """租约持有者信息（lease:run:{id} 的反序列化视图）。"""

    worker_id: str
    task_id: str
    started_at: str


@dataclass(frozen=True, slots=True)
class ControlSignal:
    """控制信号（control:run:{id} 的反序列化视图）。"""

    mode: ControlMode
    keep_partial: bool
    updated_at: str


def _lease_key(run_id: str) -> str:
    return f"lease:run:{run_id}"


def _control_key(run_id: str) -> str:
    return f"control:run:{run_id}"


class RunLease:
    """单 worker 进程视角的租约/控制键操作集合。

    Args:
        redis: 异步 Redis 客户端（建议 decode_responses=True）。
        worker_id: 本进程稳定标识（部署注入或主机名+pid 兜底）。
        ttl_seconds: 租约 TTL，须大于两倍心跳间隔。
    """

    def __init__(
        self,
        redis: Redis,
        *,
        worker_id: str,
        ttl_seconds: int = 30,
    ) -> None:
        self._redis = redis
        self._worker_id = worker_id
        self._ttl_ms = ttl_seconds * 1000

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=UTC).isoformat()

    async def try_acquire(self, run_id: str, task_id: str) -> bool:
        """以 NX 语义抢占 run 租约；已被持有（含未过期）返回 False。"""
        payload = json.dumps(
            {
                "worker_id": self._worker_id,
                "task_id": task_id,
                "started_at": self._now_iso(),
            },
            ensure_ascii=False,
        )
        acquired = await self._redis.set(_lease_key(run_id), payload, nx=True, px=self._ttl_ms)
        if acquired:
            log.info(
                "抢到在途租约",
                extra={"run_id": run_id, "worker_id": self._worker_id, "task_id": task_id},
            )
        return bool(acquired)

    async def holder(self, run_id: str) -> LeaseHolder | None:
        """读取当前租约持有者；无键/坏值返回 None。"""
        raw = await self._redis.get(_lease_key(run_id))
        return self._parse_holder(raw)

    @staticmethod
    def _parse_holder(raw: Any) -> LeaseHolder | None:
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
            worker_id = data["worker_id"]
            task_id = data["task_id"]
            started_at = data["started_at"]
        except (ValueError, KeyError, TypeError):
            return None
        if not isinstance(worker_id, str) or not isinstance(task_id, str):
            return None
        return LeaseHolder(worker_id=worker_id, task_id=task_id, started_at=str(started_at))

    async def renew(self, run_id: str, task_id: str) -> bool:
        """owner 校验后续期；非本任务持有（过期被接管等）返回 False。"""
        key = _lease_key(run_id)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                holder = self._parse_holder(await pipe.get(key))
                if holder is None or not self._is_owner(holder, task_id):
                    await pipe.reset()  # type: ignore[no-untyped-call]
                    return False
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.pexpire(key, self._ttl_ms)
                result = await pipe.execute()
        except WatchError:
            return False
        return bool(result and result[0])

    async def release(self, run_id: str, task_id: str) -> bool:
        """owner 校验后释放租约；非本任务持有不动其键，返回 False。"""
        key = _lease_key(run_id)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                holder = self._parse_holder(await pipe.get(key))
                if holder is None or not self._is_owner(holder, task_id):
                    await pipe.reset()  # type: ignore[no-untyped-call]
                    return False
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.delete(key)
                result = await pipe.execute()
        except WatchError:
            return False
        return bool(result and result[0])

    def _is_owner(self, holder: LeaseHolder, task_id: str) -> bool:
        return holder.worker_id == self._worker_id and holder.task_id == task_id

    async def read_control(self, run_id: str) -> ControlSignal | None:
        """读取停止信号；无键/坏值返回 None。"""
        raw = await self._redis.get(_control_key(run_id))
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
            mode = data["mode"]
            keep_partial = bool(data.get("keep_partial", True))
            updated_at = str(data.get("updated_at", ""))
        except (ValueError, KeyError, TypeError):
            return None
        if mode not in _MODE_RANK:
            return None
        return ControlSignal(mode=mode, keep_partial=keep_partial, updated_at=updated_at)

    async def set_control(
        self,
        run_id: str,
        mode: ControlMode,
        *,
        keep_partial: bool = True,
    ) -> bool:
        """写入/升级停止信号（只升不降）。

        - 无信号：按给定 mode 新建，带兜底 TTL；
        - 已有同级或更高级信号：原样保留（cancel 不被 pause 降级），返回 True；
        - 低级 -> 高级：升级为新 mode。

        Returns:
            事务提交成功为 True；WatchError 并发冲突为 False（调用方可重试读）。
        """
        key = _control_key(run_id)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                existing = self._read_control_raw(await pipe.get(key))
                if existing is not None and _MODE_RANK[existing["mode"]] >= _MODE_RANK[mode]:
                    # 同级/更高级信号已在：不降级，直接成功返回
                    await pipe.reset()  # type: ignore[no-untyped-call]
                    return True
                payload = json.dumps(
                    {
                        "mode": mode,
                        "keep_partial": keep_partial,
                        "updated_at": self._now_iso(),
                    },
                    ensure_ascii=False,
                )
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.set(key, payload, ex=_CONTROL_TTL_SECONDS)
                await pipe.execute()
        except WatchError:
            return False
        return True

    @staticmethod
    def _read_control_raw(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or data.get("mode") not in _MODE_RANK:
            return None
        return data

    async def clear_control(self, run_id: str) -> None:
        """run 进入终态后清除控制键（键不存在静默成功）。"""
        await self._redis.delete(_control_key(run_id))


__all__ = [
    "ControlMode",
    "ControlSignal",
    "LeaseHolder",
    "RunLease",
]
