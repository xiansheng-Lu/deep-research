"""审计日志路由占位。M1 阶段落地：操作流水、查询、导出。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("audit", "/audit")