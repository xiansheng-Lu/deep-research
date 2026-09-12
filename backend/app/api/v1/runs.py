"""研究执行路由：发起 Run、查询进度、获取报告。

对齐 LLD §6.2 / §8.2；M1 阶段交付 3 个端点：
- ``POST /runs``：创建研究运行，落库 ``ResearchRun``，后台异步调度 executor
- ``GET  /runs/{run_id}``：查询运行状态 / 当前阶段 / token 用量
- ``GET  /runs/{run_id}/report``：查询运行产出的 Markdown 报告

后台任务通过 ``asyncio.create_task`` 调度 ``executor.run_research_async``，
M1 阶段使用 in-memory LLM / 检索 stub（生产由 lifespan 注入真实 Provider）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, SettingsDep
from app.core.config import Settings
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models.project import Project
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.orchestrator.executor import run_research_async
from app.realtime.hub import RealtimeHub, get_hub
from app.schemas.reports import ReportResponse
from app.schemas.runs import CreateRunRequest, RunResponse

router = APIRouter(prefix="/runs", tags=["runs"])


def _to_run_response(run: ResearchRun) -> RunResponse:
    """把 ORM 对象转为响应视图，附带 stream_url。"""
    resp = RunResponse.model_validate(run)
    resp.stream_url = f"/api/v1/ws/runs/{run.id}/stream"
    return resp


def _to_report_response(report: Report) -> ReportResponse:
    return ReportResponse.model_validate(report)


def _token_budget_for_tier(settings: Settings, tier: str) -> int:
    """按档位返回 token 预算上限。"""
    mapping = {
        "quick": settings.quota_tier_quick_tokens,
        "standard": settings.quota_tier_standard_tokens,
        "deep": settings.quota_tier_deep_tokens,
        "extreme": settings.quota_tier_extreme_tokens,
    }
    return mapping.get(tier, settings.quota_tier_standard_tokens)


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发起研究运行",
)
async def create_run(
    payload: CreateRunRequest,
    current_user: CurrentUser,
    session: DBSession,
    settings: SettingsDep,
    request: Request,
) -> RunResponse:
    """创建研究运行并后台调度 executor。

    校验项目归属：``project_id`` 必须属于当前用户团队且 ``status='active'``。
    """
    # 校验项目归属
    project = await session.scalar(
        select(Project)
        .where(Project.id == payload.project_id)
        .where(Project.team_id == current_user.team_id)
        .where(Project.status == "active")
        .where(Project.deleted_at.is_(None))
    )
    if project is None:
        raise NotFoundError("项目不存在或不属于当前团队")

    tier = payload.tier
    token_budget = _token_budget_for_tier(settings, tier)
    template_id = payload.template_id or "generic"

    now = datetime.now(tz=UTC)
    run = ResearchRun(
        project_id=payload.project_id,
        creator_id=current_user.id,
        template_id=template_id,
        tier=tier,
        question=payload.question,
        clarification=None,
        status="pending",
        current_stage=None,
        orchestrator_state=None,
        token_used=0,
        token_budget=token_budget,
        started_at=None,
        finished_at=None,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.flush()
    # 响应视图在提交前构造，避免 expire_on_commit 后的属性刷新
    response = _to_run_response(run)

    # 后台任务使用独立会话，必须先提交使 run 行对外可见；
    # 否则在请求依赖的统一提交发生前任务即被调度，会读不到本行（RUN_NOT_FOUND 竞态）
    await session.commit()

    # 后台调度 executor（不阻塞响应）
    factory = _get_session_factory(request)
    hub = _get_hub(request)
    llm = _get_llm(request)
    retrieval_client = _get_retrieval_client(request)
    checkpointer = getattr(request.app.state, "checkpointer", None)

    asyncio.create_task(
        run_research_async(
            run_id=run.id,
            project_id=run.project_id,
            template_id=run.template_id,
            tier=run.tier,
            question=run.question,
            token_budget=run.token_budget,
            clarification=None,
            team_id=current_user.team_id,
            creator_id=current_user.id,
            trace_id=request.headers.get("x-trace-id", run.id),
            session_factory=factory,
            llm=llm,
            retrieval_client=retrieval_client,
            hub=hub,
            checkpointer=checkpointer,
        )
    )

    return response


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    summary="查询研究运行状态",
)
async def get_run(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> RunResponse:
    """查询单个研究运行的当前状态。"""
    run = await session.scalar(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .where(ResearchRun.creator_id == current_user.id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")
    return _to_run_response(run)


@router.get(
    "/{run_id}/report",
    response_model=ReportResponse,
    summary="查询研究运行的报告",
)
async def get_run_report(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ReportResponse:
    """查询研究运行产出的 Markdown 报告。"""
    # 先校验 run 归属
    run = await session.scalar(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .where(ResearchRun.creator_id == current_user.id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")

    report = await session.scalar(
        select(Report).where(Report.run_id == run_id)
    )
    if report is None:
        raise ValidationError("报告尚未生成")
    return _to_report_response(report)


# ---------------------------------------------------------------------------
# 依赖获取辅助：优先从 app.state 取（lifespan 注入），否则走全局单例
# ---------------------------------------------------------------------------


def _get_session_factory(request: Request) -> Any:
    """获取 SQLAlchemy sessionmaker；从 app.state 获取。"""
    return getattr(request.app.state, "session_factory", None)


def _get_hub(request: Request) -> RealtimeHub:
    """获取实时事件总线。"""
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        return hub
    return get_hub()


def _get_llm(request: Request) -> Any:
    """获取 LLM 客户端；M1 阶段可能为 None（节点走降级路径）。"""
    return getattr(request.app.state, "llm", None)


def _get_retrieval_client(request: Request) -> Any:
    """获取检索客户端；M1 阶段可能为 None。"""
    return getattr(request.app.state, "retrieval_client", None)


__all__ = ["router"]
