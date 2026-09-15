"""Prometheus 抓取端点（M2-3）。

``GET /metrics`` 挂应用根路径（无 /api/v1 前缀），仅在
``METRICS_ENABLED=true`` 时由 ``main.create_app`` 注册；关闭时抓取为 404。
不落业务鉴权，生产暴露面由反向代理/网络策略限制（技术方案 §9）。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Response as FastAPIResponse

from app.observability.metrics import render_latest

router = APIRouter(tags=["meta"])


@router.get("/metrics", include_in_schema=False)
async def get_metrics() -> FastAPIResponse:
    """以 Prometheus 文本暴露格式返回当前注册表快照。"""
    payload, content_type = render_latest()
    return FastAPIResponse(content=payload, media_type=content_type)


__all__ = ["router"]
