"""用户管理路由占位。M1 阶段落地：注册、邀请、个人信息、密码重置。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("users", "/users")