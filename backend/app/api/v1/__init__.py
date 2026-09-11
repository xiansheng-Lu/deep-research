"""v1 版本路由聚合。

每个子模块暴露一个 APIRouter，由本模块统一 include_router。
WebSocket 路由从 realtime 模块引入，与 REST 路由共用 /api/v1 前缀。
"""

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    conflicts,
    connectors,
    knowledge,
    projects,
    reports,
    runs,
    teams,
    templates,
    users,
)
from app.realtime.ws import router as ws_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(teams.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(runs.router)
api_v1_router.include_router(conflicts.router)
api_v1_router.include_router(reports.router)
api_v1_router.include_router(knowledge.router)
api_v1_router.include_router(connectors.router)
api_v1_router.include_router(templates.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(ws_router)

__all__ = ["api_v1_router"]
