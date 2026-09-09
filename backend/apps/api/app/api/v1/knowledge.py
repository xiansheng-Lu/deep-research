"""知识库路由占位。M1 阶段落地：条目 CRUD、向量检索、上传与解析。"""

from fastapi import APIRouter

from app.api.v1._placeholder import build_router

router = build_router("knowledge", "/knowledge")