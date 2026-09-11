"""Pydantic Schema 集合。

按业务域分子模块：
    auth / users / teams / projects / runs / conflicts /
    reports / knowledge / connectors / templates / audit / common
"""

from app.schemas.common import ErrorResponse, HealthResponse, PageMeta, PaginatedResponse
from app.schemas.projects import (
    CreateProjectRequest,
    ProjectResponse,
    ProjectStatus,
    ProjectTier,
)
from app.schemas.reports import ReportResponse, ReportStatus
from app.schemas.runs import CreateRunRequest, RunResponse, RunStatus, RunTier

__all__ = [
    # common
    "ErrorResponse",
    "HealthResponse",
    "PageMeta",
    "PaginatedResponse",
    # projects
    "CreateProjectRequest",
    "ProjectResponse",
    "ProjectStatus",
    "ProjectTier",
    # reports
    "ReportResponse",
    "ReportStatus",
    # runs
    "CreateRunRequest",
    "RunResponse",
    "RunStatus",
    "RunTier",
]
