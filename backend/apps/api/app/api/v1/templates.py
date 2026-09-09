"""模板管理路由占位。M1 阶段落地：项目模板、报告模板、变量校验。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("templates", "/templates")