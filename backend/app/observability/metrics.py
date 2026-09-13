"""Prometheus 指标定义与打点入口（M2-3，对齐技术方案 §9）。

设计要点：

- ``REGISTRY`` 为模块级全局单例（不在 ``create_app`` 内构造），测试创建多
  app 实例时复用同一注册表，避免 ``Duplicated timeseries in CollectorRegistry``。
- HTTP 中间件 ``path`` 标签使用路由模板（如 ``/api/v1/runs/{run_id}``）而非
  原始 URL，杜绝 run_id/证据 ID 造成的高基数；未匹配路由统一记 ``unmatched``。
- LLM 指标的 ``run_id`` / ``stage`` 经 ContextVar 从执行器驱动期写入
  （``app.core.context``），非图上下文（如 agents 直调）缺失时标 ``unknown``。
- ``/metrics`` 路由是否注册由 ``METRICS_ENABLED`` 控制；指标对象本身始终存在，
  WS/检索等业务打点不依赖开关，关闭时只是没有抓取出口。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.core.context import current_run_id, current_stage

#: 模块级全局注册表（唯一事实源，禁止在应用工厂内重复 new）
REGISTRY = CollectorRegistry()

#: 标签缺失时的统一占位值
_UNKNOWN = "unknown"

# --- HTTP 面 ---------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求累计次数（按方法/路由模板/状态码）",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求处理耗时（秒，按方法/路由模板）",
    labelnames=("method", "path"),
    registry=REGISTRY,
)

# --- LLM 面 ----------------------------------------------------------------

llm_token_usage_total = Counter(
    "llm_token_usage_total",
    "LLM 结构化调用累计 token 用量（按模型/阶段/run）",
    labelnames=("model", "stage", "run_id"),
    registry=REGISTRY,
)

# --- 实时面 ----------------------------------------------------------------

ws_active_connections = Gauge(
    "ws_active_connections",
    "当前活跃 WebSocket 连接数",
    registry=REGISTRY,
)

# --- 检索面 ----------------------------------------------------------------

#: 单子问题检索终态：成功 / 失败 / 证据不足
_EVIDENCE_FETCH_STATUSES = frozenset({"succeeded", "failed", "evidence_short"})

evidence_fetch_total = Counter(
    "evidence_fetch_total",
    "单子问题检索终态累计次数（按检索来源/终态）",
    labelnames=("source_type", "status"),
    registry=REGISTRY,
)


def record_http_request(method: str, path_template: str, status_code: int, duration_s: float) -> None:
    """记录一次 HTTP 请求计数与耗时。"""
    path = path_template or "unmatched"
    http_requests_total.labels(method=method, path=path, status=str(status_code)).inc()
    http_request_duration_seconds.labels(method=method, path=path).observe(duration_s)


def record_llm_usage(*, model: str, total_tokens: int) -> None:
    """记录一次 LLM 结构化调用的 token 用量；run/stage 取自 ContextVar。"""
    if total_tokens <= 0:
        return
    stage = current_stage.get() or _UNKNOWN
    run_id = current_run_id.get() or _UNKNOWN
    llm_token_usage_total.labels(
        model=model or _UNKNOWN,
        stage=stage,
        run_id=run_id,
    ).inc(total_tokens)


def record_evidence_fetch(*, source_type: str, status: str) -> None:
    """记录单子问题检索终态；非预期终态收敛到 failed，防止标签失控。"""
    label_status = status if status in _EVIDENCE_FETCH_STATUSES else "failed"
    evidence_fetch_total.labels(
        source_type=source_type or _UNKNOWN,
        status=label_status,
    ).inc()


def render_latest() -> tuple[bytes, str]:
    """返回 ``(prometheus 文本载荷, Content-Type)``。"""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response


def install_http_metrics(app: FastAPI) -> None:
    """挂载轻量 HTTP 指标中间件（仅在 METRICS_ENABLED=true 时安装）。"""

    @app.middleware("http")
    async def _metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path_template = getattr(route, "path", "")
            record_http_request(
                request.method,
                path_template,
                status_code,
                time.perf_counter() - start,
            )


__all__ = [
    "REGISTRY",
    "http_requests_total",
    "http_request_duration_seconds",
    "llm_token_usage_total",
    "ws_active_connections",
    "evidence_fetch_total",
    "record_http_request",
    "record_llm_usage",
    "record_evidence_fetch",
    "render_latest",
    "install_http_metrics",
]
