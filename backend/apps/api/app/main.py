"""FastAPI 应用入口。

启动命令（开发态）：``uvicorn app.main:app --reload`` 或 ``deep-research-api``
"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.api.v1 import api_v1_router
from app.core.config import Settings, get_settings
from app.core.context import bind_request_context
from app.core.exceptions import AppError
from app.core.lifespan import lifespan as default_lifespan
from app.core.logging import get_logger
from app.schemas.common import HealthResponse
from app import __version__

log = get_logger("app.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    """工厂函数：便于测试时构造独立实例。"""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        async with default_lifespan(app):
            yield

    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=_lifespan,
    )
    app.state.settings = cfg

    # CORS（默认放开开发环境，生产环境由部署侧配置反向代理收敛）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if cfg.is_dev else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _trace_middleware(request: Request, call_next: Any):
        """为每个请求注入 trace_id，并写入响应头。"""
        trace_id = request.headers.get("x-trace-id") or _new_trace_id()
        bind_request_context(trace_id=trace_id)
        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        return response

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError):
        """统一业务异常响应。"""
        log.warning("业务异常", extra={"code": exc.code, "message": exc.message})
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/healthz", response_model=HealthResponse, tags=["meta"])
    async def healthz() -> HealthResponse:
        return HealthResponse(service=cfg.app_name, version=__version__, timestamp=_utcnow())

    app.include_router(api_v1_router)
    return app


app = create_app()


def run() -> None:
    """``deep-research-api`` 脚本入口。"""
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.app_host,
        port=cfg.app_port,
        reload=cfg.is_dev,
    )


def _new_trace_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc)