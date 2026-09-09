"""研究执行路由占位。M1 阶段落地：发起 Run、查询进度、人机介入、终止。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("runs", "/runs")