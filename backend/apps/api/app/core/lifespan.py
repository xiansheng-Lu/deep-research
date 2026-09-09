"""FastAPI 生命周期：启动/关闭钩子统一入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭时执行初始化与清理。"""
    settings: Settings = app.state.settings
    configure_logging(settings)
    log = get_logger("app.lifespan")

    log.info("应用启动", extra={"env": settings.app_env})
    try:
        # 后续阶段在此挂接：DB engine、Redis、Provider registry、WS Hub、Tracing 等
        yield
    finally:
        log.info("应用关闭")