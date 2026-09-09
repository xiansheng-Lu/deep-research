"""研究报告路由占位。M1 阶段落地：报告详情、流式生成（SSE）、导出、分享。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("reports", "/reports")