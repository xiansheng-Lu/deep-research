"""研究项目路由占位。M1 阶段落地：项目 CRUD、模板应用、归档。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("projects", "/projects")