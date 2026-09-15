"""Celery worker 进程依赖装配（M2-8b）。

复用 lifespan 的纯函数装配 DB/检查点/LLM/检索，额外强制 Redis 装配：
RealtimeHub 走 Redis Pub/Sub、注册表走租约/控制键。worker 不导入 FastAPI
层代码（阶段方案 §15「不 import app.api」约束）。

生产 worker 必须能连到 Redis：启动 ping 失败即快速失败（§16.1），不静默
退回内存态（内存态下跨进程信号全部丢失）。
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.lifespan import _build_llm_client
from app.core.logging import configure_logging, get_logger
from app.core.redis import build_redis, close_redis
from app.db.session import SessionFactory, init_engine, shutdown_engine
from app.orchestrator.checkpoint import build_checkpointer, close_checkpointer
from app.orchestrator.lease import RunLease
from app.orchestrator.registry import RedisRunRegistry, set_run_registry
from app.provider.client import LLMClient
from app.realtime.hub import RealtimeHub, set_default_hub
from app.retrieval.client import RetrievalClient

log = get_logger("workers.bootstrap")


@dataclass(slots=True)
class WorkerDeps:
    """worker 进程内单个任务执行所需的全部依赖。"""

    settings: Settings
    session_factory: Any
    checkpointer: Any
    hub: RealtimeHub
    redis: Redis
    lease: RunLease
    registry: RedisRunRegistry
    worker_id: str
    llm: LLMClient | None
    retrieval_client: RetrievalClient | None


@asynccontextmanager
async def worker_context() -> AsyncIterator[WorkerDeps]:
    """worker 任务级装配上下文：强校验 Redis，退出时逆序释放全部资源。"""
    settings = get_settings()
    configure_logging(settings)

    if not settings.worker_enable_redis:
        raise RuntimeError("worker 启动要求 WORKER_ENABLE_REDIS=true（不得静默退回内存态）")

    redis = build_redis(settings)
    try:
        await redis.ping()
    except Exception as exc:  # noqa: BLE001 - 统一转启动错误，附原始原因
        await close_redis(redis)
        raise RuntimeError(f"worker 无法连接 Redis（{settings.redis_url}），快速失败") from exc

    worker_id = settings.worker_id or f"{socket.gethostname()}-{os.getpid()}"
    log.info("worker 装配 Redis 双态组件", extra={"worker_id": worker_id})

    hub = RealtimeHub(redis)
    set_default_hub(hub)
    lease = RunLease(redis, worker_id=worker_id, ttl_seconds=settings.run_lease_ttl_seconds)
    registry = RedisRunRegistry(lease)
    set_run_registry(registry)

    db: SessionFactory = init_engine(settings)
    session_factory = db.maker()
    checkpointer, checkpointer_pool = await build_checkpointer(settings)
    llm = _build_llm_client(settings, log)
    retrieval_client = RetrievalClient.from_settings(settings)

    deps = WorkerDeps(
        settings=settings,
        session_factory=session_factory,
        checkpointer=checkpointer,
        hub=hub,
        redis=redis,
        lease=lease,
        registry=registry,
        worker_id=worker_id,
        llm=llm,
        retrieval_client=retrieval_client,
    )
    try:
        yield deps
    finally:
        log.info("worker 释放任务依赖", extra={"worker_id": worker_id})
        if retrieval_client is not None:
            with suppress(Exception):
                await retrieval_client.aclose()
        with suppress(Exception):
            await close_checkpointer(checkpointer_pool)
        with suppress(Exception):
            await hub.aclose()
        await close_redis(redis)
        shutdown_engine()


__all__ = ["WorkerDeps", "worker_context"]
