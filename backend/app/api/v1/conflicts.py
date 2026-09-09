"""冲突审视路由占位。M1 阶段落地：冲突列表、评论、裁决、版本对比。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("conflicts", "/conflicts")