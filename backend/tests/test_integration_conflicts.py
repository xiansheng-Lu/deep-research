"""M2-2 Task 5 冲突落库 / WS 帧 / 挂起 / 恢复幂等的真实 Postgres 集成测试。

CI 无外部数据库时自动跳过；本地以 WSL2 Docker 内 dr_postgres 为目标：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research

隔离方式：独立 schema ``m22_task5``（search_path 追加 public 解析 pgvector），
setup 重建、teardown DROP CASCADE。

覆盖：
- TR-5.1：low/medium 冲突真库落 resolved（resolved_at 非空）、无 Verdict、不挂起；
- TR-5.2：high 冲突真库落 awaiting_human、每冲突一帧 conflict.detected、
  全图执行 run paused 且帧序在 run.finished(paused) 之前；
- TR-5.5：恢复重跑（await_human + critic 回流）不重复插入 Conflict/Verdict。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import create_all_in_schema
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 导入以注册全部表到 Base.metadata
    Conflict,
    Evidence,
    Project,
    ResearchRun,
    SubQuestion,
    Team,
    User,
    Verdict,
)
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.edges import decide_after_critique
from app.orchestrator.executor import (
    _build_initial_state,
    _compile_for_deps,
    _drive_to_terminal,
)
from app.orchestrator.nodes import await_human, critic
from app.orchestrator.persistence import ensure_stage_rows
from app.orchestrator.schemas import (
    ClarificationSchema,
    ConflictDetectionSchema,
    ConflictPairResult,
    SubQuestionItem,
    SubQuestionListSchema,
)
from app.orchestrator.state import EvidenceDict
from app.provider.client import LLMClient, StructuredCompletion
from app.realtime.hub import RealtimeHub
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient

pytestmark = pytest.mark.integration

_SCHEMA = "m22_task5"
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"


@pytest.fixture(scope="module")
def db_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL", _DEFAULT_DSN)


@pytest.fixture
async def session_factory(db_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """构造指向独立 schema 的会话工厂；数据库不可达时 skip（CI 场景）。"""
    engine = create_async_engine(
        db_dsn,
        pool_size=2,
        connect_args={"server_settings": {"search_path": f"{_SCHEMA},public"}},
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
        async with engine.begin() as conn:
            # 显式 schema 建表，避免 checkfirst 命中 public 同名表（隔离失效）
            await conn.run_sync(create_all_in_schema, _SCHEMA)
    except Exception as exc:  # noqa: BLE001 - CI 无 PG 时整体跳过
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过冲突链路集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_run(session: AsyncSession) -> tuple[str, str]:
    """插入最小外键链 Team→User→Project→ResearchRun，返回 (run_id, user_id)。"""
    team = Team(id=new_ulid(), name="冲突集成测试团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="冲突集成测试用户",
        role="owner",
    )
    project = Project(
        id=new_ulid(),
        team_id=team.id,
        owner_id=user.id,
        name="冲突集成测试项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="standard",
        question="2026 年中国光伏新增装机规模各方口径是否冲突？",
        token_budget=100_000,
    )
    session.add_all([team, user, project, run])
    await session.flush()
    return run.id, user.id


def _evidence_dict(
    ev_id: str,
    sq_id: str,
    *,
    title: str,
    snippet: str,
    credibility: str,
) -> EvidenceDict:
    return {
        "id": ev_id,
        "sub_question_id": sq_id,
        "url": f"https://example.com/{ev_id}",
        "domain": "example.com",
        "title": title,
        "snippet": snippet,
        "source_type": "news",
        "source_level": "secondary",
        "credibility": credibility,
        "fingerprint": f"fp-{ev_id}",
        "published_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
        "fetched_at": datetime(2026, 9, 2, tzinfo=UTC).isoformat(),
    }


async def _insert_evidence_rows(
    session: AsyncSession,
    run_id: str,
    ev_a: EvidenceDict,
    ev_b: EvidenceDict,
) -> None:
    """插入冲突外键依赖的子问题与双方证据行。"""
    session.add(
        SubQuestion(
            id=ev_a["sub_question_id"],
            run_id=run_id,
            question="光伏新增装机口径",
            depends_on=[],
            status="succeeded",
            evidence_count=2,
        )
    )
    # 显式先落子问题：保证 evidence FK 在同一 flush 内可见（与生产节点顺序一致）
    await session.flush()
    for ev in (ev_a, ev_b):
        session.add(
            Evidence(
                id=ev["id"],
                run_id=run_id,
                sub_question_id=ev["sub_question_id"],
                url=ev["url"],
                domain=ev["domain"],
                title=ev["title"],
                snippet=ev["snippet"],
                source_type="news",
                source_level="secondary",
                credibility=ev["credibility"],
                relevance_score=0.8,
                fingerprint=ev["fingerprint"],
                published_at=datetime(2026, 9, 1, tzinfo=UTC),
                fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
            )
        )
    await session.flush()


async def _count_conflicts(session: AsyncSession, run_id: str) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM conflicts WHERE run_id = :rid").bindparams(rid=run_id)
            )
        ).scalar_one()
    )


async def _count_verdicts(session: AsyncSession, run_id: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM verdicts v JOIN conflicts c ON v.conflict_id = c.id "
                    "WHERE c.run_id = :rid"
                ).bindparams(rid=run_id)
            )
        ).scalar_one()
    )


class _SingleHighConflictLLM(LLMClient):
    """critic 专用假 LLM：对唯一候选对返回 high 冲突。"""

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
        return StructuredCompletion(
            parsed=ConflictDetectionSchema(
                results=[
                    ConflictPairResult(
                        pair_index=0,
                        is_conflict=True,
                        claim="2026 年光伏新增装机规模",
                        type="methodological",
                        severity="high",
                        reason="统计口径不同导致关键数字矛盾",
                    )
                ]
            ),
            usage={"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
            model="stub-conflict-llm",
            raw="",
        )


class _OfflineConflictRetrieval(RetrievalClient):
    """返回两条同议题、结论相反的固定命中。"""

    def __init__(self) -> None:
        # 跳过父类 __init__：不要求 primary/backup provider
        pass

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:  # noqa: ARG002
        shared = "2026年中国光伏新增装机规模统计数据"
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title=f"{shared}国家统计局发布",
                url="https://stats.gov.cn/pv-2026",
                snippet=f"{shared}显示新增装机210GW，同比增长45%",
                score=0.95,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title=f"{shared}行业协会并网口径",
                url="https://assoc.org.cn/pv-2026",
                snippet=f"{shared}按并网口径实际仅120GW，含口径差异说明",
                score=0.9,
            ),
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(hits)


class _RoutingStubLLM(LLMClient):
    """全链路假 LLM：按 tags 分流 clarify/decompose/critique 的结构化返回。"""

    def __init__(self) -> None:
        self.tags_seen: list[str] = []

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
        tag = ",".join(tags or [])
        self.tags_seen.append(tag)
        usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        if schema is ClarificationSchema:
            return StructuredCompletion(
                parsed=ClarificationSchema(
                    requires_user_input=False,
                    questions=[],
                    defaults={},
                    structured_question={
                        "goal": "光伏装机口径冲突核查",
                        "scope": "2026 年",
                        "key_concepts": [],
                    },
                ),
                usage=usage,
                model="stub-route-llm",
                raw="",
            )
        if schema is SubQuestionListSchema:
            return StructuredCompletion(
                parsed=SubQuestionListSchema(
                    sub_questions=[
                        SubQuestionItem(question="2026 年光伏新增装机的官方与行业口径各是多少", depends_on=[])
                    ]
                ),
                usage=usage,
                model="stub-route-llm",
                raw="",
            )
        return StructuredCompletion(
            parsed=ConflictDetectionSchema(
                results=[
                    ConflictPairResult(
                        pair_index=0,
                        is_conflict=True,
                        claim="2026 年光伏新增装机规模",
                        type="methodological",
                        severity="high",
                        reason="统计口径不同导致数字矛盾",
                    )
                ]
            ),
            usage=usage,
            model="stub-route-llm",
            raw="",
        )


async def test_medium_conflict_persists_resolved_without_verdict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TR-5.1（AC-3 真库部分）：启发式 medium 冲突落 resolved、无 Verdict、门控放行。"""
    async with session_factory() as session:
        run_id, _ = await _seed_run(session)
        sq_id = new_ulid()
        title = "2026 光伏装机 200GW 统计数据"
        ev_a = _evidence_dict(new_ulid(), sq_id, title=title, snippet=title, credibility="A")
        ev_b = _evidence_dict(new_ulid(), sq_id, title=title, snippet=title, credibility="D")
        await _insert_evidence_rows(session, run_id, ev_a, ev_b)

        deps = NodeDeps(run_id=run_id, team_id="t1", trace_id="tr1", db_session=session)
        state: dict[str, Any] = {"run_id": run_id, "standardized_evidence": [ev_a, ev_b]}
        patch = await critic.run(state, deps=deps)
        await session.flush()

        assert len(patch["conflicts"]) == 1
        assert patch["conflicts"][0]["status"] == "resolved"
        assert decide_after_critique(patch) == "cost_checkpoint"  # type: ignore[arg-type]

        rows = (
            await session.execute(
                text("SELECT status, resolved_at, severity FROM conflicts WHERE run_id = :rid").bindparams(
                    rid=run_id
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "resolved"
        assert rows[0].resolved_at is not None
        assert rows[0].severity == "medium"
        assert await _count_verdicts(session, run_id) == 0
        await session.rollback()


async def test_high_conflict_persists_and_emits_frame(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TR-5.2 节点+真库：high 落 awaiting_human（resolved_at 空），一帧嵌套载荷。"""
    async with session_factory() as session:
        run_id, _ = await _seed_run(session)
        sq_id = new_ulid()
        shared = "2026年中国光伏新增装机规模统计数据"
        ev_a = _evidence_dict(
            new_ulid(), sq_id, title=f"{shared}国家统计局", snippet=f"{shared}210GW", credibility="A"
        )
        ev_b = _evidence_dict(
            new_ulid(), sq_id, title=f"{shared}行业协会", snippet=f"{shared}120GW", credibility="C"
        )
        await _insert_evidence_rows(session, run_id, ev_a, ev_b)

        hub = RealtimeHub()
        frames: list[dict[str, Any]] = []

        async def _collect() -> None:
            async for event in hub.subscribe(f"runs:{run_id}"):
                frames.append(event)
                if event["type"] == "conflict.detected":
                    break

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0.05)

        llm = _SingleHighConflictLLM()
        deps = NodeDeps(run_id=run_id, team_id="t1", trace_id="tr1", db_session=session, llm=llm, hub=hub)
        patch = await critic.run(
            {"run_id": run_id, "standardized_evidence": [ev_a, ev_b]},
            deps=deps,
        )
        await asyncio.wait_for(collector, timeout=2.0)
        await session.flush()

        assert patch["conflicts"][0]["status"] == "awaiting_human"
        assert decide_after_critique(patch) == "await_human"  # type: ignore[arg-type]

        rows = (
            await session.execute(
                text(
                    "SELECT status, resolved_at, type, severity FROM conflicts WHERE run_id = :rid"
                ).bindparams(rid=run_id)
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "awaiting_human"
        assert rows[0].resolved_at is None
        assert rows[0].type == "methodological"
        assert rows[0].severity == "high"

        assert len(frames) == 1
        payload = frames[0]["payload"]
        assert payload["id"] == patch["conflicts"][0]["id"]
        assert payload["status"] == "awaiting_human"
        assert payload["severity"] == "high"
        assert frames[0]["type"] == "conflict.detected"
        await session.rollback()


async def test_full_graph_pauses_with_frame_before_finished(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TR-5.2 全链路：critic 挂起 → run paused，冲突帧先于 run.finished(paused)。"""
    async with session_factory() as session:
        run_id, _ = await _seed_run(session)
        run = await session.get(ResearchRun, run_id)
        assert run is not None

        hub = RealtimeHub()
        frames: list[dict[str, Any]] = []

        async def _collect() -> None:
            async for event in hub.subscribe(f"runs:{run_id}"):
                frames.append(event)
                if event["type"] == "run.finished":
                    break

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0.05)

        deps = NodeDeps(
            run_id=run_id,
            team_id="t1",
            trace_id="tr1",
            llm=_RoutingStubLLM(),
            retrieval_client=_OfflineConflictRetrieval(),
        )
        graph = _compile_for_deps(deps, InMemorySaver())
        initial_state = _build_initial_state(
            run_id=run_id,
            project_id=run.project_id,
            question=run.question,
            template_id="generic",
            tier="standard",
            token_budget=100_000,
            clarification=None,
            trace_id="tr1",
        )
        await _drive_to_terminal(
            graph=graph,
            graph_input=initial_state,
            run=run,
            template_id="generic",
            session=session,
            hub=hub,
            deps=deps,
            fallback_state=initial_state,
            stage_rows=await ensure_stage_rows(session, run_id=run_id),
        )
        await asyncio.wait_for(collector, timeout=10.0)

        assert run.status == "paused"
        assert run.current_stage == "critique"
        assert await _count_conflicts(session, run_id) == 1
        rows = (
            await session.execute(
                text("SELECT status FROM conflicts WHERE run_id = :rid").bindparams(rid=run_id)
            )
        ).all()
        assert rows[0].status == "awaiting_human"

        types = [f["type"] for f in frames]
        assert "conflict.detected" in types
        finished = next(f for f in frames if f["type"] == "run.finished")
        assert finished["status"] == "paused"
        assert types.index("conflict.detected") < types.index("run.finished")


async def test_resume_replay_does_not_duplicate_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TR-5.5：回流（await_human 解析裁决 + critic 收敛）重放不产生重复行。"""
    async with session_factory() as session:
        run_id, user_id = await _seed_run(session)
        sq_id = new_ulid()
        shared = "2026年中国光伏新增装机规模统计数据"
        ev_a = _evidence_dict(
            new_ulid(), sq_id, title=f"{shared}国家统计局", snippet=f"{shared}210GW", credibility="A"
        )
        ev_b = _evidence_dict(
            new_ulid(), sq_id, title=f"{shared}行业协会", snippet=f"{shared}120GW", credibility="C"
        )
        await _insert_evidence_rows(session, run_id, ev_a, ev_b)

        hub = RealtimeHub()
        llm = _SingleHighConflictLLM()
        deps = NodeDeps(run_id=run_id, team_id="t1", trace_id="tr1", db_session=session, llm=llm, hub=hub)

        # 首次 critic：检出 1 条 high 冲突并落库
        first_state: dict[str, Any] = {"run_id": run_id, "standardized_evidence": [ev_a, ev_b]}
        patch1 = await critic.run(first_state, deps=deps)
        conflict_id = patch1["conflicts"][0]["id"]
        await session.flush()
        assert await _count_conflicts(session, run_id) == 1

        # 模拟裁决 REST 端点已写 Verdict 行（Task 7 接线，本测试直接插行）
        session.add(
            Verdict(
                id=new_ulid(),
                conflict_id=conflict_id,
                user_id=user_id,
                choice="both",
                reason="双方口径不同，观点并存",
                additional_note="写入局限说明",
            )
        )
        await session.flush()

        # 恢复：await_human 解析 human_input.answers.verdicts，critic 回流收敛
        human_input = {
            "answers": {
                "verdicts": {
                    conflict_id: {
                        "choice": "both",
                        "reason": "双方口径不同，观点并存",
                        "additional_note": "写入局限说明",
                        "user_id": user_id,
                    }
                }
            }
        }
        resumed_state: dict[str, Any] = {
            "run_id": run_id,
            "interrupt_reason": "critique",
            "human_input": human_input,
            "verdicts": [],
        }
        ah_patch = await await_human.run(resumed_state, deps=deps)  # type: ignore[arg-type]
        assert len(ah_patch["verdicts"]) == 1

        critic_state: dict[str, Any] = {
            "run_id": run_id,
            "standardized_evidence": [ev_a, ev_b],
            "report_claims": patch1["report_claims"],
            "conflicts": patch1["conflicts"],
            "verdicts": ah_patch["verdicts"],
        }
        patch2 = await critic.run(critic_state, deps=deps)
        assert patch2["conflicts"][0]["status"] == "resolved"
        # both：双方 claim 保留且带并存标记
        assert len(patch2["report_claims"]) == 2
        assert all(c.get("divergence_flags") == [conflict_id] for c in patch2["report_claims"])
        assert "interrupt_reason" not in patch2

        # 重放：相同 human_input 再次经过 await_human，裁决去重不追加
        replay_state = {**resumed_state, "verdicts": ah_patch["verdicts"]}
        ah_patch2 = await await_human.run(replay_state, deps=deps)  # type: ignore[arg-type]
        assert len(ah_patch2["verdicts"]) == 1

        await session.flush()
        assert await _count_conflicts(session, run_id) == 1
        assert await _count_verdicts(session, run_id) == 1
