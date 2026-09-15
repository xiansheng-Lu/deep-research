"""FastAPI 生命周期：启动/关闭钩子统一入口。"""

import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from redis.asyncio import Redis as AsyncRedis

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import build_redis, close_redis
from app.db.session import init_engine, shutdown_engine
from app.orchestrator.checkpoint import build_checkpointer, close_checkpointer
from app.orchestrator.lease import RunLease
from app.orchestrator.registry import RedisRunRegistry, set_run_registry
from app.provider.client import LLMClient
from app.realtime.hub import RealtimeHub, get_hub, set_default_hub
from app.retrieval.client import RetrievalClient


def _build_llm_client(settings: Settings, log: Any) -> LLMClient | None:
    """配置了主模型密钥时构造 LLMClient；否则返回 None（节点走降级路径）。"""
    if not settings.llm_primary_api_key.get_secret_value():
        log.warning("未配置 LLM_PRIMARY_API_KEY，LLM 节点将走降级路径")
        return None
    client = LLMClient.from_registry()
    if client._primary is None:  # noqa: SLF001 - 启动期唯一可判定注册结果的位置
        log.warning("LLM Provider 构造失败（检查 base_url/model/密钥），节点将走降级路径")
        return None
    log.info(
        "LLM Provider 已注入",
        extra={"model": settings.llm_primary_model, "base_url": settings.llm_primary_base_url},
    )
    return client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭时执行初始化与清理。"""
    settings: Settings = app.state.settings
    configure_logging(settings)
    log = get_logger("app.lifespan")

    # DB engine 初始化
    session_factory = init_engine(settings)
    app.state.session_factory = session_factory.maker()

    # 编排检查点：PostgresSaver（支持跨请求 HITL 恢复）；失败回退应用级内存 saver
    checkpointer, checkpointer_pool = await build_checkpointer(settings)
    app.state.checkpointer = checkpointer
    app.state.checkpointer_pool = checkpointer_pool

    # RealtimeHub / 注册表注入：M2-8b 起 worker_enable_redis=true 时 API 与
    # worker 分进程，hub 走 Redis Pub/Sub 桥接、注册表走租约/控制键；
    # false（离线/单进程）保持内存态。Redis 连接惰性建立，ping 不阻塞启动：
    # 故障时事件发布降级日志、控制写入显式报错（阶段方案 §21）。
    redis_client: AsyncRedis | None = None
    if settings.worker_enable_redis:
        redis_client = build_redis(settings)
        hub = RealtimeHub(redis_client)
        set_default_hub(hub)
        api_worker_id = f"api-{socket.gethostname()}-{os.getpid()}"
        set_run_registry(
            RedisRunRegistry(
                RunLease(redis_client, worker_id=api_worker_id, ttl_seconds=settings.run_lease_ttl_seconds)
            )
        )
        log.info("API 进程启用 Redis 双态 Hub/注册表", extra={"node": api_worker_id})
    else:
        hub = get_hub()
    app.state.hub = hub
    app.state.redis = redis_client

    # LLM / 检索客户端注入：配置了有效密钥即走真实链路，否则节点按既定降级路径执行
    app.state.llm = _build_llm_client(settings, log)
    app.state.retrieval_client = RetrievalClient.from_settings(settings)
    log.info(
        "公域检索 Provider 注入状态",
        extra={
            "provider": settings.web_search_provider,
            "enabled": app.state.retrieval_client is not None,
        },
    )

    log.info("应用启动", extra={"env": settings.app_env})
    try:
        yield
    finally:
        log.info("应用关闭")
        # 释放检索 Provider 的 httpx 连接池
        retrieval = app.state.retrieval_client
        if retrieval is not None:
            with suppress(Exception):
                await retrieval.aclose()
        # 关闭 checkpointer 关联的 Postgres 连接池
        with suppress(Exception):
            await close_checkpointer(getattr(app.state, "checkpointer_pool", None))
        # 关闭 Redis 桥接（pubsub 与连接）
        if redis_client is not None:
            with suppress(Exception):
                await hub.aclose()
            await close_redis(redis_client)
        shutdown_engine()
