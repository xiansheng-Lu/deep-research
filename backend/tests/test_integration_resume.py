"""M2-2 Task 8 裁决后自动恢复闭环：真实 Postgres + AsyncPostgresSaver 端到端集成。

CI 无外部数据库时自动跳过；DSN：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research

独立 schema ``m22_task8``：业务表由 conftest.create_all_in_schema 建入；
LangGraph checkpoint 表经 psycopg ``options=-c search_path`` 也建入同 schema，
teardown DROP CASCADE 一并清理。

覆盖：
- TR-8.1：2 条 high 冲突 paused，首条裁决后仍 paused，末条裁决后后台自动恢复
  至 succeeded，报告含「分歧与局限」段；
- TR-8.2：critique 恢复以新构建 saver/图实例完成（模拟进程重启）；另覆盖
  clarify 挂起经新实例恢复；
- TR-8.3：恢复执行抛错时 run=failed 且推 run.failed 帧，无静默卡死。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from conftest import create_all_in_schema
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps as api_deps
from app.api.v1.conflicts import router as conflicts_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 注册全部表
    Project,
    ResearchRun,
    Team,
    User,
)
from app.orchestrator.executor import resume_research_async, run_research_async
from app.orchestrator.schemas import (
    ClarificationQuestion,
    ClarificationSchema,
    ConflictDetectionSchema,
    ConflictPairResult,
    SubQuestionItem,
    SubQuestionListSchema,
)
from app.provider.client import LLMClient, StructuredCompletion
from app.realtime.hub import RealtimeHub
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient
from app.schemas.runs import HumanInput

pytestmark = pytest.mark.integration

_SCHEMA = "m22_task8"
_ASYNC_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research",
)
_PSYCOPG_DSN = _ASYNC_DSN.replace("+asyncpg", "", 1)

_USAGE = {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40}


@pytest.fixture
async def context() -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(
        _ASYNC_DSN,
        pool_size=2,
        connect_args={"server_settings": {"search_path": f"{_SCHEMA},public"}},
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
        async with engine.begin() as conn:
            await conn.run_sync(create_all_in_schema, _SCHEMA)
    except Exception as exc:  # noqa: BLE001 - CI 无 PG 时整体跳过
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过裁决恢复集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    app = FastAPI(default_response_class=ORJSONResponse)

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError) -> ORJSONResponse:
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    app.include_router(conflicts_router, prefix="/api/v1")

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[api_deps.db_session] = _override_session
    hub = RealtimeHub()
    app.state.session_factory = maker
    app.state.hub = hub
    app.state.llm = None
    app.state.retrieval_client = None
    app.state.checkpointer = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "maker": maker, "engine": engine, "app": app, "hub": hub}

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    await engine.dispose()


# ---------------------------------------------------------------------------
# 假 LLM / 检索
# ---------------------------------------------------------------------------


class _TwoConflictLLM(LLMClient):
    """critique 固定返回 2 条 high 冲突（对应候选对 0/1）。"""

    def __init__(self) -> None:
        pass

    async def complete_structured(  # type: ignore[override]
        self,
        *,
        messages: list[Any],
        schema: type,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
    ) -> StructuredCompletion:
        if schema is SubQuestionListSchema:
            return StructuredCompletion(
                parsed=SubQuestionListSchema(
                    sub_questions=[SubQuestionItem(question="两个议题的口径各是什么", depends_on=[])]
                ),
                usage=_USAGE,
                model="stub",
                raw="",
            )
        if schema is ConflictDetectionSchema:
            return StructuredCompletion(
                parsed=ConflictDetectionSchema(
                    results=[
                        ConflictPairResult(
                            pair_index=0,
                            is_conflict=True,
                            claim="光伏新增装机规模",
                            type="methodological",
                            severity="high",
                            reason="统计口径不同数字矛盾",
                        ),
                        ConflictPairResult(
                            pair_index=1,
                            is_conflict=True,
                            claim="欧洲央行七月利率走向",
                            type="perspective",
                            severity="high",
                            reason="加息与维持不变的判断对立",
                        ),
                    ]
                ),
                usage=_USAGE,
                model="stub",
                raw="",
            )
        return StructuredCompletion(
            parsed=ClarificationSchema(
                requires_user_input=False,
                questions=[],
                defaults={},
                structured_question={"goal": "核查两个议题", "scope": "2026 年 7 月", "key_concepts": []},
            ),
            usage=_USAGE,
            model="stub",
            raw="",
        )


class _ClarifyThenCompleteLLM(LLMClient):
    """首轮澄清挂起；恢复后不追问，critique 不判冲突。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured(  # type: ignore[override]
        self,
        *,
        messages: list[Any],
        schema: type,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
    ) -> StructuredCompletion:
        self.calls += 1
        if schema is ClarificationSchema:
            if self.calls == 1:
                return StructuredCompletion(
                    parsed=ClarificationSchema(
                        requires_user_input=True,
                        questions=[ClarificationQuestion(key="scope", text="研究范围限定在哪个市场？")],
                        defaults={},
                        structured_question={},
                    ),
                    usage=_USAGE,
                    model="stub",
                    raw="",
                )
            return StructuredCompletion(
                parsed=ClarificationSchema(
                    requires_user_input=False,
                    questions=[],
                    defaults={},
                    structured_question={"goal": "利率走向核查", "scope": "欧洲市场", "key_concepts": []},
                ),
                usage=_USAGE,
                model="stub",
                raw="",
            )
        if schema is SubQuestionListSchema:
            return StructuredCompletion(
                parsed=SubQuestionListSchema(
                    sub_questions=[SubQuestionItem(question="欧洲央行七月利率决议结果", depends_on=[])]
                ),
                usage=_USAGE,
                model="stub",
                raw="",
            )
        return StructuredCompletion(
            parsed=ConflictDetectionSchema(results=[]),
            usage=_USAGE,
            model="stub",
            raw="",
        )


class _FourHitRetrieval(RetrievalClient):
    """两对同议题、跨议题词面不相交的固定命中。"""

    def __init__(self) -> None:
        pass

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:  # noqa: ARG002
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="国家统计局发布光伏新增装机规模数据",
                url="https://stats.gov.cn/pv",
                snippet="光伏新增装机规模达到二百一十吉瓦同比大增四成五",
                score=0.95,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="行业协会披露光伏新增装机规模口径",
                url="https://assoc.org.cn/pv",
                snippet="光伏新增装机规模按并网口径仅一百二十吉瓦差距明显",
                score=0.9,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="欧洲央行七月份利率决议公布",
                url="https://ecb.europa.eu/hike",
                snippet="欧洲央行七月份利率决议加息二十五个基点符合鹰派预期",
                score=0.9,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="投行解读欧洲央行七月份利率决议",
                url="https://bank.example/ecb",
                snippet="欧洲央行七月份利率决议维持不变降息预期落空鸽派误判",
                score=0.9,
            ),
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(hits)


class _TwoHitRetrieval(RetrievalClient):
    """同可信度、不对立的互补命中（配合空冲突 LLM）。"""

    def __init__(self) -> None:
        pass

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:  # noqa: ARG002
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="欧洲央行七月份利率决议官方公告",
                url="https://ecb.europa.eu/1",
                snippet="决议维持三大关键利率不变符合市场一致预期",
                score=0.9,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="财经媒体梳理欧洲央行七月份利率决议",
                url="https://news.example/ecb",
                snippet="公告同时释放后续货币政策将依通胀数据调整的信号",
                score=0.85,
            ),
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(hits)


class _BrokenSaver(InMemorySaver):
    """检查点写入即抛错：模拟恢复链路基础设施故障（TR-8.3）。

    注意 ``aupdate_state`` 是图（CompiledGraph）的方法，saver 侧真正承接的是
    ``aput``/``aput_writes``；在恢复写入线程状态时即失败。
    """

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated checkpoint failure during resume")

    async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated checkpoint failure during resume")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _make_pg_saver() -> tuple[Any, AsyncConnectionPool]:
    """新建独立 AsyncPostgresSaver（checkpoint 表建入 m22_task8 schema）。"""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # pool 泛型参数与 AsyncPostgresSaver 声明不兼容（同生产 checkpoint.py 口径标 Any）
    pool: Any = AsyncConnectionPool(
        _PSYCOPG_DSN,
        open=False,
        kwargs={"autocommit": True, "options": f"-c search_path={_SCHEMA}"},
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver, pool


async def _seed_run(session: AsyncSession) -> tuple[ResearchRun, User]:
    team = Team(id=new_ulid(), name="恢复集成测试团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="恢复集成测试用户",
        role="owner",
    )
    project = Project(
        id=new_ulid(),
        team_id=team.id,
        owner_id=user.id,
        name="恢复集成测试项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="standard",
        question="光伏装机口径与欧洲央行利率两个议题核查",
        token_budget=100_000,
    )
    session.add_all([team, user, project, run])
    await session.flush()
    return run, user


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id, settings=get_settings())}"}


async def _start_run(
    context: dict[str, Any],
    run: ResearchRun,
    *,
    llm: LLMClient,
    retrieval: RetrievalClient,
    saver: Any,
) -> None:
    """以首次执行入口驱动 run 到 paused（真检查点）。"""
    await run_research_async(
        run_id=run.id,
        project_id=run.project_id,
        template_id="generic",
        tier="standard",
        question=run.question,
        token_budget=100_000,
        clarification=None,
        team_id="t1",
        creator_id="u1",
        trace_id=f"tr-{run.id}",
        session_factory=context["maker"],
        llm=llm,
        retrieval_client=retrieval,
        hub=context["hub"],
        checkpointer=saver,
    )


async def _wait_status(
    maker: async_sessionmaker[AsyncSession], run_id: str, expected: str, wait_s: float = 30.0
) -> ResearchRun:
    deadline = asyncio.get_event_loop().time() + wait_s
    while True:
        async with maker() as session:
            run = await session.get(ResearchRun, run_id)
            if run is not None and run.status == expected:
                return run
        if asyncio.get_event_loop().time() > deadline:
            current = run.status if run is not None else None
            raise AssertionError(f"等待 run={run_id} 状态 {expected} 超时，当前 {current}")
        await asyncio.sleep(0.1)


async def _collect_frames(hub: RealtimeHub, run_id: str, count: int) -> list[dict[str, Any]]:
    """收集指定数量的终态帧（run.finished）。"""
    frames: list[dict[str, Any]] = []
    async for event in hub.subscribe(f"runs:{run_id}"):
        if event.get("type") == "run.finished":
            frames.append(event)
            if len(frames) >= count:
                break
    return frames


# ---------------------------------------------------------------------------
# TR-8.1 逐条裁决自动恢复
# ---------------------------------------------------------------------------


async def test_verdicts_resume_run_to_succeeded(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    app: FastAPI = context["app"]
    hub: RealtimeHub = context["hub"]

    saver, pool = await _make_pg_saver()
    try:
        async with maker() as session:
            run, user = await _seed_run(session)
            run_id = run.id
            await session.commit()

        app.state.llm = _TwoConflictLLM()
        app.state.retrieval_client = _FourHitRetrieval()
        app.state.checkpointer = saver

        frame_collector = asyncio.create_task(_collect_frames(hub, run_id, 2))
        await asyncio.sleep(0.05)

        await _start_run(context, run, llm=app.state.llm, retrieval=app.state.retrieval_client, saver=saver)
        async with maker() as session:
            paused = await session.get(ResearchRun, run_id)
            assert paused is not None and paused.status == "paused"
            assert paused.current_stage == "critique"

        # 两条 high 冲突已落库
        list_resp = await client.get(f"/api/v1/runs/{run_id}/conflicts", headers=_auth(user.id))
        assert list_resp.status_code == 200
        conflicts = list_resp.json()
        assert len(conflicts) == 2
        assert all(c["status"] == "awaiting_human" for c in conflicts)
        cid_1, cid_2 = conflicts[0]["id"], conflicts[1]["id"]

        # 首条裁决：仍有一条待裁决，run 保持 paused，不触发恢复
        first = await client.post(
            f"/api/v1/conflicts/{cid_1}/verdict",
            headers=_auth(user.id),
            json={"choice": "evidence_a", "reason": "采信统计局限口径"},
        )
        assert first.status_code == 200
        await asyncio.sleep(0.2)
        async with maker() as session:
            run_after_first = await session.get(ResearchRun, run_id)
            assert run_after_first is not None and run_after_first.status == "paused"

        # 末条裁决：剩余清零，后台自动恢复
        second = await client.post(
            f"/api/v1/conflicts/{cid_2}/verdict",
            headers=_auth(user.id),
            json={"choice": "both", "reason": "两种判断并存", "additional_note": "决议口径需注明"},
        )
        assert second.status_code == 200

        succeeded_run = await _wait_status(maker, run_id, "succeeded")
        assert succeeded_run.current_stage == "report"
        frames = await asyncio.wait_for(frame_collector, timeout=5.0)
        assert [f["status"] for f in frames] == ["paused", "succeeded"]

        # 报告含未消解分歧段（both），单边收敛冲突只入摘要
        async with maker() as session:
            report_md = (
                await session.execute(
                    text("SELECT content_md FROM reports WHERE run_id = :rid").bindparams(rid=run_id)
                )
            ).scalar_one()
        assert "分歧与局限" in report_md
        assert "欧洲央行七月利率走向" in report_md
        assert "双方观点并存" in report_md
        assert "决议口径需注明" in report_md
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# TR-8.2 新实例恢复（clarify 挂起 + 全新 saver 连接）
# ---------------------------------------------------------------------------


async def test_clarify_resume_with_new_saver_instance(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    hub: RealtimeHub = context["hub"]

    saver_1, pool_1 = await _make_pg_saver()
    try:
        async with maker() as session:
            run, _ = await _seed_run(session)
            run_id = run.id
            await session.commit()

        llm = _ClarifyThenCompleteLLM()
        frame_collector = asyncio.create_task(_collect_frames(hub, run_id, 2))
        await asyncio.sleep(0.05)

        # 首次执行：clarify 要求追问 → 挂在 await_human 之前
        await _start_run(context, run, llm=llm, retrieval=_TwoHitRetrieval(), saver=saver_1)
        async with maker() as session:
            paused = await session.get(ResearchRun, run_id)
            assert paused is not None and paused.status == "paused"
            assert paused.current_stage == "clarify"
    finally:
        await pool_1.close()

    # 模拟进程重启：用新连接池、新 saver 实例（同一 Postgres 线程状态）
    saver_2, pool_2 = await _make_pg_saver()
    try:
        await resume_research_async(
            run_id=run_id,
            human_input={"answers": {"scope": "欧洲市场", "timeframe": "2026 年 7 月"}},
            session_factory=maker,
            checkpointer=saver_2,
            llm=llm,
            retrieval_client=_TwoHitRetrieval(),
            hub=hub,
        )
        succeeded_run = await _wait_status(maker, run_id, "succeeded")
        assert succeeded_run.current_stage == "report"
        frames = await asyncio.wait_for(frame_collector, timeout=5.0)
        assert [f["status"] for f in frames] == ["paused", "succeeded"]
    finally:
        await pool_2.close()


# ---------------------------------------------------------------------------
# TR-8.3 恢复故障落 failed
# ---------------------------------------------------------------------------


async def test_resume_failure_marks_run_failed(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    app: FastAPI = context["app"]
    hub: RealtimeHub = context["hub"]

    saver, pool = await _make_pg_saver()
    try:
        async with maker() as session:
            run, user = await _seed_run(session)
            run_id = run.id
            await session.commit()

        llm = _TwoConflictLLM()
        app.state.llm = llm
        app.state.retrieval_client = _FourHitRetrieval()
        app.state.checkpointer = saver

        frame_collector = asyncio.create_task(_collect_frames(hub, run_id, 2))
        await asyncio.sleep(0.05)

        await _start_run(context, run, llm=llm, retrieval=app.state.retrieval_client, saver=saver)

        conflicts = (await client.get(f"/api/v1/runs/{run_id}/conflicts", headers=_auth(user.id))).json()
        cid_1, cid_2 = conflicts[0]["id"], conflicts[1]["id"]
        await client.post(
            f"/api/v1/conflicts/{cid_1}/verdict",
            headers=_auth(user.id),
            json={"choice": "evidence_a", "reason": "先采一方"},
        )
        # 末条裁决前把检查点换成故障实例：恢复协程写入检查点即抛错
        app.state.checkpointer = _BrokenSaver()
        fault = await client.post(
            f"/api/v1/conflicts/{cid_2}/verdict",
            headers=_auth(user.id),
            json={"choice": "both", "reason": "并存"},
        )
        assert fault.status_code == 200

        failed_run = await _wait_status(maker, run_id, "failed")
        assert failed_run.error_code == "RuntimeError"
        await asyncio.sleep(0.3)
        frames = await asyncio.wait_for(frame_collector, timeout=5.0)
        assert frames[-1]["type"] == "run.finished"
        assert frames[-1]["status"] == "failed"
        assert frames[-1]["error_code"] == "RuntimeError"
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# 交接单 §9.1 回归：runs_control 恢复服务（预翻转 running）→ 真实 executor
# 组合链（不 patch executor），覆盖 answers / proceed 两分支
# ---------------------------------------------------------------------------


def _service_request(context: dict[str, Any], saver: Any, llm: Any, retrieval: Any) -> Any:
    """构造 resume_run 所需的最小 Request（仅消费 app.state.*）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory=context["maker"],
                checkpointer=saver,
                llm=llm,
                retrieval_client=retrieval,
                hub=context["hub"],
            )
        )
    )


async def test_resume_service_answers_branch_drives_to_succeeded(context: dict[str, Any]) -> None:
    """澄清挂起经 runs_control.resume_run（非 executor 直连）恢复至 succeeded。

    回归交接单 §9.1：服务层先条件 UPDATE paused→running，修复前 executor
    守卫只认 paused 会直接 return，run 永久卡 running。
    """
    from app.services import runs_control

    maker: async_sessionmaker[AsyncSession] = context["maker"]
    hub: RealtimeHub = context["hub"]

    saver, pool = await _make_pg_saver()
    try:
        async with maker() as session:
            run, user = await _seed_run(session)
            run_id = run.id
            await session.commit()

        llm = _ClarifyThenCompleteLLM()
        retrieval = _TwoHitRetrieval()
        frame_collector = asyncio.create_task(_collect_frames(hub, run_id, 2))
        await asyncio.sleep(0.05)

        await _start_run(context, run, llm=llm, retrieval=retrieval, saver=saver)
        async with maker() as session:
            paused = await session.get(ResearchRun, run_id)
            assert paused is not None and paused.status == "paused"
            assert paused.current_stage == "clarify"

        # 走真实恢复服务（条件 UPDATE 预翻转 + 真实 executor 续跑，不打补丁）
        async with maker() as session:
            run_row = await session.get(ResearchRun, run_id)
            assert run_row is not None
            await runs_control.resume_run(
                session,
                run=run_row,
                human_input=HumanInput.model_validate({"answers": {"scope": "欧洲市场"}}),
                user_id=user.id,
                team_id=user.team_id,
                request=_service_request(context, saver, llm, retrieval),
            )

        succeeded_run = await _wait_status(maker, run_id, "succeeded")
        assert succeeded_run.current_stage == "report"
        frames = await asyncio.wait_for(frame_collector, timeout=10.0)
        assert [f["status"] for f in frames] == ["paused", "succeeded"]

        # 终态后恢复占位必须已由真实协程注销（注册表无残留）
        from app.orchestrator.registry import get_run_registry

        assert await get_run_registry().is_active(run_id) is False
    finally:
        await pool.close()


async def test_resume_service_proceed_branch_after_cost_gate(context: dict[str, Any]) -> None:
    """90% 成本闸门挂起（current_stage=user_intervention 前置点）后，

    经恢复服务空 body（kind=proceed）恢复至 succeeded（AC-6 + §9.1 proceed 分支）。
    """
    from test_executor import _BudgetedStructuredLLM

    from app.services import runs_control

    maker: async_sessionmaker[AsyncSession] = context["maker"]
    hub: RealtimeHub = context["hub"]

    saver, pool = await _make_pg_saver()
    try:
        async with maker() as session:
            run, user = await _seed_run(session)
            run_id = run.id
            await session.commit()

        # 预算 100：clarify+decompose 两次结构化调用各 60（离线替身 critic
        # 不调 LLM）→ 120 > 100*0.9，闸门在 critique 后判定挂起
        llm = _BudgetedStructuredLLM(tokens_per_call=60)
        retrieval = _TwoHitRetrieval()
        frame_collector = asyncio.create_task(_collect_frames(hub, run_id, 2))
        await asyncio.sleep(0.05)

        # 小预算首跑：直接调用执行器（生产 create_run 路径同样直连）
        await run_research_async(
            run_id=run_id,
            project_id=run.project_id,
            template_id="generic",
            tier="standard",
            question=run.question,
            token_budget=100,
            clarification=None,
            team_id="t1",
            creator_id=user.id,
            trace_id=f"tr-{run_id}",
            session_factory=maker,
            llm=llm,
            retrieval_client=retrieval,
            hub=hub,
            checkpointer=saver,
        )
        paused_run = await _wait_status(maker, run_id, "paused")
        assert paused_run.token_used >= 100

        # 空 body 恢复（等价 kind=proceed）：经真实恢复服务
        async with maker() as session:
            run_row = await session.get(ResearchRun, run_id)
            assert run_row is not None
            await runs_control.resume_run(
                session,
                run=run_row,
                human_input=None,
                user_id=user.id,
                team_id=user.team_id,
                request=_service_request(context, saver, llm, retrieval),
            )

        succeeded_run = await _wait_status(maker, run_id, "succeeded")
        assert succeeded_run.current_stage == "report"
        frames = await asyncio.wait_for(frame_collector, timeout=10.0)
        assert [f["status"] for f in frames] == ["paused", "succeeded"]
    finally:
        await pool.close()
