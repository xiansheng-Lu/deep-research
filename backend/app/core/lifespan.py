"""FastAPI 生命周期：启动/关闭钩子统一入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.db.session import init_engine, shutdown_engine
from app.realtime.hub import get_hub


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭时执行初始化与清理。"""
    settings: Settings = app.state.settings
    configure_logging(settings)
    log = get_logger("app.lifespan")

    # DB engine 初始化
    session_factory = init_engine(settings)
    app.state.session_factory = session_factory.maker()

    # RealtimeHub 注入
    app.state.hub = get_hub()

    # LLM / 检索客户端：M1 阶段暂不注入，节点走降级路径
    app.state.llm = None
    app.state.retrieval_client = None

    log.info("应用启动", extra={"env": settings.app_env})
    try:
        yield
    finally:
        log.info("应用关闭")
        shutdown_engine()
