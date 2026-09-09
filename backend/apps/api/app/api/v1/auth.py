"""鉴权路由占位。M1 阶段落地：登录、刷新、登出、当前用户。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("auth", "/auth")