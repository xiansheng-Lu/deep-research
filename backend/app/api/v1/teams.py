"""团队与成员路由占位。M1 阶段落地：团队 CRUD、成员角色、邀请与移除。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("teams", "/teams")