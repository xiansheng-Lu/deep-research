"""外部连接器路由占位。M1 阶段落地：列表、配置、OAuth 绑定、同步。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("connectors", "/connectors")