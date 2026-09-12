"""FastAPI 生命周期：启动/关闭钩子统一入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.db.session import init_engine, shutdown_engine
from app.orchestrator.checkpoint import build_checkpointer, close_checkpointer
from app.provider.client import LLMClient
from app.realtime.hub import get_hub
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

    # RealtimeHub 注入
    app.state.hub = get_hub()

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
        shutdown_engine()
