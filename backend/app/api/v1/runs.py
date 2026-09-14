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

from fastapi import APIRouter, Header, Query, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, SettingsDep
from app.core.config import Settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models.project import Project
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.orchestrator.executor import run_research_async
from app.orchestrator.registry import get_run_registry
from app.quota.tiers import classify_cost_level
from app.realtime.hub import RealtimeHub, get_hub
from app.schemas.common import MAX_PAGE_SIZE, PaginatedResponse, build_page
from app.schemas.dashboard import (
    CostSnapshotResponse,
    EvidenceResponse,
    StageResponse,
    SubQuestionResponse,
)
from app.schemas.reports import ReportResponse
from app.schemas.runs import (
    CancelRunRequest,
    CreateRunRequest,
    InterruptInfo,
    InterventionAction,
    PauseRunRequest,
    ResumeRunRequest,
    RunControlResponse,
    RunResponse,
    RunStatus,
)
from app.services import dashboard, runs_control
from app.services.conflicts import get_owned_run
from app.services.interventions import submit_intervention

log = get_logger("api.runs")

router = APIRouter(prefix="/runs", tags=["runs"])


def _to_run_response(run: ResearchRun) -> RunResponse:
    """把 ORM 对象转为响应视图，附带 stream_url。"""
    resp = RunResponse.model_validate(run)
    resp.stream_url = f"/api/v1/ws/runs/{run.id}/stream"
    return resp


def _to_report_response(report: Report) -> ReportResponse:
    return ReportResponse.model_validate(report)


def _normalize_key(value: str | None) -> str | None:
    """空白 Idempotency-Key 归一为缺省（不启用幂等）。"""
    if value is None:
        return None
    key = value.strip()
    return key or None


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


@router.post(
    "/{run_id}/pause",
    response_model=RunControlResponse,
    summary="暂停研究运行（软暂停）",
)
async def pause_run(
    run_id: str,
    payload: PauseRunRequest,  # reason 随控制动作审计落档（T4 接入写入）
    current_user: CurrentUser,
    session: DBSession,
) -> RunControlResponse:
    """软暂停运行中的研究。

    - 仅 ``running`` 且存在在途执行协程时可暂停；已暂停 409 RUN_ALREADY_PAUSED，
      其他终态/孤儿 running 409 RUN_NOT_PAUSABLE；
    - 暂停为协作式：响应返回后协程在最近 super-step 边界落 paused 并推
      ``run.finished(status=paused)``，恢复走 POST /resume（M2-5 T2）。
    """
    run = await get_owned_run(session, run_id, current_user.id)
    run = await runs_control.pause_run(
        session,
        run=run,
        registry=get_run_registry(),
        team_id=current_user.team_id,
        user_id=current_user.id,
        reason=payload.reason,
    )
    return RunControlResponse(run_id=run.id, status=run.status)


@router.post(
    "/{run_id}/cancel",
    response_model=RunControlResponse,
    summary="取消研究运行（硬中断）",
)
async def cancel_run(
    run_id: str,
    payload: CancelRunRequest,  # reason 随控制动作审计落档（T4 接入写入）
    current_user: CurrentUser,
    session: DBSession,
    request: Request,
) -> RunControlResponse:
    """硬取消研究；对 succeeded/failed/cancelled 幂等返回当前状态。

    ``keep_partial=true``（默认）时若已有报告草稿则保留并在响应带回 id。
    """
    run = await get_owned_run(session, run_id, current_user.id)
    run, partial_report_id = await runs_control.cancel_run(
        session,
        run=run,
        registry=get_run_registry(),
        hub=_get_hub(request),
        team_id=current_user.team_id,
        user_id=current_user.id,
        keep_partial=payload.keep_partial,
        reason=payload.reason,
    )
    return RunControlResponse(
        run_id=run.id,
        status=run.status,
        partial_report_id=partial_report_id,
    )


@router.post(
    "/{run_id}/resume",
    response_model=RunControlResponse,
    summary="恢复研究运行（人类输入统一入口）",
)
async def resume_run(
    run_id: str,
    payload: ResumeRunRequest,
    current_user: CurrentUser,
    session: DBSession,
    request: Request,
) -> RunControlResponse:
    """从暂停/澄清/裁决/成本挂起点恢复。

    空 body 或 ``{human_input:{kind:"proceed"}}`` 为纯继续；澄清挂起必须带
    非空 answers；载荷校验与 409/422 语义见 M2-5 方案 §5.3。
    """
    run = await get_owned_run(session, run_id, current_user.id)
    run = await runs_control.resume_run(
        session,
        run=run,
        human_input=payload.human_input,
        user_id=current_user.id,
        team_id=current_user.team_id,
        request=request,
    )
    return RunControlResponse(run_id=run.id, status=run.status)


@router.post(
    "/{run_id}/intervene",
    response_model=RunControlResponse,
    summary="运行中主动介入（追加追问/剔除证据）",
)
async def intervene_run(
    run_id: str,
    action: InterventionAction,
    current_user: CurrentUser,
    session: DBSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=64),
) -> RunControlResponse:
    """running 中的受控介入；动作语义与可用阶段见 M2-5 方案 §5.5。"""
    run = await get_owned_run(session, run_id, current_user.id)
    run = await submit_intervention(
        session,
        run=run,
        user_id=current_user.id,
        team_id=current_user.team_id,
        action=action,
        idempotency_key=_normalize_key(idempotency_key),
    )
    return RunControlResponse(run_id=run.id, status=run.status)


@router.get(
    "",
    response_model=PaginatedResponse[RunResponse],
    summary="查询研究运行列表（创建者维度）",
)
async def list_runs(
    current_user: CurrentUser,
    session: DBSession,
    status_filter: RunStatus | None = Query(default=None, alias="status"),
    project_id: str | None = Query(default=None, max_length=26),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedResponse[RunResponse]:
    """分页返回当前用户创建的研究运行（created_at 倒序）。

    - ``status``：六态单值过滤，非法枚举由 FastAPI 判 422；
    - ``project_id``：再过滤且校验团队归属，非本团队项目 404。
    """
    rows, total = await dashboard.list_creator_runs(
        session,
        user_id=current_user.id,
        team_id=current_user.team_id,
        status=status_filter,
        project_id=project_id,
        page=page,
        page_size=page_size,
    )
    return build_page(
        [_to_run_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    summary="查询研究运行状态",
)
async def get_run(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
    request: Request,
    settings: SettingsDep,
) -> RunResponse:
    """查询单个研究运行的当前状态。

    paused@clarify 时附加 interrupt 澄清上下文（M2-5 §5.4，供刷新恢复澄清卡）。
    """
    run = await session.scalar(
        select(ResearchRun).where(ResearchRun.id == run_id).where(ResearchRun.creator_id == current_user.id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")
    response = _to_run_response(run)
    if run.status == "paused":
        response.interrupt = await _load_interrupt_info(request, run_id, settings)
    return response


async def _load_interrupt_info(request: Request, run_id: str, settings: Settings) -> InterruptInfo | None:
    """从检查点只读澄清挂起上下文；非澄清挂起/读取失败均返回 None（不阻塞详情）。"""
    from app.orchestrator.executor import read_run_interrupt

    try:
        interrupt = await read_run_interrupt(
            run_id=run_id,
            session_factory=_get_session_factory(request),
            checkpointer=getattr(request.app.state, "checkpointer", None),
            llm=_get_llm(request),
            retrieval_client=_get_retrieval_client(request),
        )
    except Exception as exc:  # noqa: BLE001 - 详情读取挂起上下文失败不致命
        log.warning("读取澄清挂起上下文失败", extra={"run_id": run_id, "error": repr(exc)})
        return None
    if not interrupt or interrupt.get("reason") != "clarify":
        return None
    payload = interrupt.get("payload") or {}
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    return InterruptInfo(
        reason="clarify",
        questions=questions,
        defaults=payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {},
        expires_in_seconds=settings.clarification_expires_seconds,
    )


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
        select(ResearchRun).where(ResearchRun.id == run_id).where(ResearchRun.creator_id == current_user.id)
    )
    if run is None:
        raise NotFoundError("研究运行不存在")

    report = await session.scalar(select(Report).where(Report.run_id == run_id))
    if report is None:
        raise ValidationError("报告尚未生成")
    return _to_report_response(report)


@router.get(
    "/{run_id}/stages",
    response_model=list[StageResponse],
    summary="查询运行阶段时间线",
)
async def list_run_stages(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[StageResponse]:
    """固定六阶段顺序返回；历史无阶段行的 run 返回空数组。"""
    rows = await dashboard.list_run_stages(session, run_id=run_id, user_id=current_user.id)
    return [StageResponse.model_validate(r) for r in rows]


@router.get(
    "/{run_id}/sub-questions",
    response_model=list[SubQuestionResponse],
    summary="查询运行的子问题列表",
)
async def list_run_sub_questions(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[SubQuestionResponse]:
    """按 created_at 升序返回 run 下全部子问题。"""
    rows = await dashboard.list_run_sub_questions(session, run_id=run_id, user_id=current_user.id)
    return [SubQuestionResponse.model_validate(r) for r in rows]


@router.get(
    "/{run_id}/evidence",
    response_model=PaginatedResponse[EvidenceResponse],
    summary="分页查询运行的证据池",
)
async def list_run_evidence(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
    sub_question_id: str | None = Query(default=None, max_length=26),
    include_excluded: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedResponse[EvidenceResponse]:
    """证据池分页（fetched_at 倒序）；默认剔除用户已排除证据。"""
    rows, total = await dashboard.list_run_evidence(
        session,
        run_id=run_id,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        sub_question_id=sub_question_id,
        include_excluded=include_excluded,
    )
    return build_page(
        [EvidenceResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{run_id}/evidence/{evidence_id}",
    response_model=EvidenceResponse,
    summary="查询单条证据详情（全文懒加载）",
)
async def get_run_evidence(
    run_id: str,
    evidence_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> EvidenceResponse:
    """单条证据详情；归属不符/不存在统一 404。"""
    evidence = await dashboard.get_run_evidence(
        session,
        run_id=run_id,
        evidence_id=evidence_id,
        user_id=current_user.id,
    )
    return EvidenceResponse.model_validate(evidence)


@router.get(
    "/{run_id}/cost/snapshot",
    response_model=CostSnapshotResponse,
    summary="查询运行实时成本快照",
)
async def get_run_cost_snapshot(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> CostSnapshotResponse:
    """成本快照直接读 run 行；level 与实时帧共用同一阈值派生。"""
    run, ratio = await dashboard.get_cost_snapshot(session, run_id=run_id, user_id=current_user.id)
    return CostSnapshotResponse(
        used=int(run.token_used),
        budget=int(run.token_budget),
        # 展示值保留三位小数；级别用未舍入 ratio 判定，避免临界值口径不一致
        ratio=round(ratio, 3),
        level=classify_cost_level(ratio),
    )


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
