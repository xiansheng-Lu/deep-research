"""M2-5 用户介入真实 Postgres 集成测试（AC-10/AC-12/AC-14/AC-17）。

CI 无外部数据库时自动跳过；本地以 WSL2 Docker 内 Postgres 为目标：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research

隔离 schema 使用 ``m25_`` 前缀（``m25_orm`` 走 ORM 建表，``m25_migration`` 走
alembic 迁移建表），search_path 追加 public 解析 pgvector 类型；setup 重建、
teardown DROP CASCADE，跑后无残留。

覆盖：
- AC-12：pause/resume/cancel/intervene.* 五个控制动作经 service 真实落
  audit_entries（action/payload/target 正确）；
- AC-10/AC-14：run_interventions 真库 JSONB 往返、(run_id, idempotency_key)
  唯一索引拒绝重复键且允许 NULL 并存；
- AC-14：0004 迁移 upgrade head/downgrade -1 后表与唯一索引随迁移增减。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import create_all_in_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 导入以注册表到 Base.metadata
    Project,
    ResearchRun,
    Team,
    User,
)
from app.db.models.audit import AuditEntry as AuditEntryModel
from app.db.models.evidence import Evidence
from app.db.models.intervention import RunIntervention
from app.db.models.run import SubQuestion
from app.orchestrator.registry import get_run_registry
from app.realtime.hub import RealtimeHub
from app.schemas.runs import HumanInput, InterventionAction
from app.services import runs_control
from app.services.interventions import submit_intervention

pytestmark = pytest.mark.integration

_ORM_SCHEMA = "m25_orm"
_MIGRATION_SCHEMA = "m25_migration"
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"
_BACKEND_DIR = Path(__file__).resolve().parents[1]


async def _recreate_schema(dsn: str, schema: str) -> None:
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {schema}"))
    finally:
        await engine.dispose()


@pytest.fixture
async def orm_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """m25_orm schema 的 ORM 会话工厂（建表走 metadata，不经迁移）。"""
    dsn = os.environ.get("TEST_DATABASE_URL", _DEFAULT_DSN)
    engine = create_async_engine(
        dsn,
        pool_size=3,
        connect_args={"server_settings": {"search_path": f"{_ORM_SCHEMA},public"}},
    )
    try:
        await _recreate_schema(dsn, _ORM_SCHEMA)
        async with engine.begin() as conn:
            await conn.run_sync(create_all_in_schema, _ORM_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - CI 无 PG 时整体跳过
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过 M2-5 集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_chain(
    session: AsyncSession,
    *,
    run_status: str = "running",
    current_stage: str | None = "retrieve",
) -> tuple[ResearchRun, User, Team]:
    """插入 Team→User→Project→ResearchRun 最小外键链，返回 ORM 行。"""
    team = Team(id=new_ulid(), name="M25集成团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="M25集成用户",
        role="owner",
    )
    project = Project(
        id=new_ulid(),
        team_id=team.id,
        owner_id=user.id,
        name="M25集成项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="quick",
        question="M2-5 用户介入集成测试问题",
        status=run_status,
        current_stage=current_stage,
        token_budget=100_000,
    )
    session.add_all([team, user, project, run])
    await session.flush()
    return run, user, team


async def _seed_subquestion(session: AsyncSession, run_id: str) -> SubQuestion:
    subq = SubQuestion(
        id=new_ulid(),
        run_id=run_id,
        question="原子问题",
        depends_on=[],
        status="running",
        evidence_count=0,
    )
    session.add(subq)
    await session.flush()
    return subq


async def _seed_evidence(session: AsyncSession, run_id: str, subq_id: str) -> Evidence:
    now = datetime.now(tz=UTC)
    evidence = Evidence(
        id=new_ulid(),
        run_id=run_id,
        sub_question_id=subq_id,
        url="https://example.gov/m25",
        domain="example.gov",
        title="M25 证据",
        snippet="摘要内容",
        content=None,
        source_type="official_doc",
        source_level="primary",
        credibility="A",
        relevance_score=0.9,
        fingerprint=f"fp-m25-{new_ulid()}",
        published_at=now,
        fetched_at=now,
        metadata_={},
        excluded_by_user=False,
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def _audit_actions(session: AsyncSession, *, team_id: str, run_id: str) -> dict[str, AuditEntryModel]:
    """读取某 run 下全部审计行，按 action 建索引（同动作只断言一条）。"""
    from sqlalchemy import select

    rows = await session.scalars(
        select(AuditEntryModel)
        .where(AuditEntryModel.team_id == team_id)
        .where(AuditEntryModel.target_id == run_id)
    )
    return {entry.action: entry for entry in rows.all()}


@pytest.fixture
def _clean_registry() -> AsyncIterator[None]:
    """隔离进程级运行任务注册表单例。"""
    registry = get_run_registry()
    registry.clear()
    yield
    registry.clear()


# ---------------------------------------------------------------------------
# AC-12：五个控制动作真实落 audit_entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_actions_write_audit_entries_real_db(
    orm_factory: async_sessionmaker[AsyncSession],
    _clean_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pause→cancel 与 running 两类介入、paused resume 均真实写审计。"""
    registry = get_run_registry()
    hub = RealtimeHub()

    # --- run A：running → pause → cancel ---
    async with orm_factory() as session:
        run_a, user_a, team_a = await _seed_chain(session, run_status="running")
        run_a_id, user_a_id, team_a_id = run_a.id, user_a.id, team_a.id
        await session.commit()

    gate = asyncio.Event()
    # 非执行器的占位在途任务：pause 仅需句柄可寻址、可 cancel（协程收尾由执行器
    # 单测覆盖，此处只验证控制服务的状态机与审计落库）
    wait_task = asyncio.create_task(gate.wait())
    registry.register(run_a_id, wait_task)

    async with orm_factory() as session:
        run_a_row = await session.get(ResearchRun, run_a_id)
        assert run_a_row is not None
        paused = await runs_control.pause_run(
            session,
            run=run_a_row,
            registry=registry,
            team_id=team_a_id,
            user_id=user_a_id,
            reason="m25-暂停",
        )
        assert paused.status == "paused"

    # 清理被 cancel 的占位任务
    await asyncio.gather(wait_task, return_exceptions=True)

    async with orm_factory() as session:
        run_a_row = await session.get(ResearchRun, run_a_id)
        cancelled, _ = await runs_control.cancel_run(
            session,
            run=run_a_row,  # type: ignore[arg-type]
            registry=registry,
            hub=hub,
            team_id=team_a_id,
            user_id=user_a_id,
            reason="m25-取消",
        )
        assert cancelled.status == "cancelled"

    # --- run B：running@retrieve 两类介入 ---
    async with orm_factory() as session:
        run_b, user_b, team_b = await _seed_chain(session)
        subq = await _seed_subquestion(session, run_b.id)
        evidence = await _seed_evidence(session, run_b.id, subq.id)
        run_b_id, user_b_id, team_b_id = run_b.id, user_b.id, team_b.id
        subq_id, evidence_id = subq.id, evidence.id
        await session.commit()

    async with orm_factory() as session:
        run_b_row = await session.get(ResearchRun, run_b_id)
        await submit_intervention(
            session,
            run=run_b_row,  # type: ignore[arg-type]
            user_id=user_b_id,
            team_id=team_b_id,
            action=InterventionAction(
                type="ask_followup",
                payload={"sub_question_id": subq_id, "question": "补充追问"},
            ),
            idempotency_key="m25-key-1",
        )
        await submit_intervention(
            session,
            run=run_b_row,  # type: ignore[arg-type]
            user_id=user_b_id,
            team_id=team_b_id,
            action=InterventionAction(
                type="exclude_evidence",
                payload={"evidence_id": evidence_id, "reason": "不采信"},
            ),
            idempotency_key="m25-key-2",
        )

    async with orm_factory() as session:
        from sqlalchemy import select

        marked = await session.get(Evidence, evidence_id)
        assert marked is not None and marked.excluded_by_user is True
        rows = await session.scalars(select(RunIntervention).where(RunIntervention.run_id == run_b_id))
        statuses = {r.type: r.status for r in rows}
        assert statuses == {"ask_followup": "pending", "exclude_evidence": "applied"}

    # --- run C：paused 手动暂停 → resume proceed（拦截图读取与后台调度）---
    async def _read_none(**_kwargs: Any) -> None:
        return None

    async def _no_resume(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.orchestrator.executor.read_run_interrupt", _read_none)
    monkeypatch.setattr("app.orchestrator.executor.resume_research_async", _no_resume)

    async with orm_factory() as session:
        run_c, user_c, team_c = await _seed_chain(session, run_status="paused", current_stage=None)
        run_c_id, user_c_id, team_c_id = run_c.id, user_c.id, team_c.id
        await session.commit()

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory=orm_factory,
                checkpointer=None,
                llm=None,
                retrieval_client=None,
                hub=hub,
            )
        )
    )
    async with orm_factory() as session:
        run_c_row = await session.get(ResearchRun, run_c_id)
        resumed = await runs_control.resume_run(
            session,
            run=run_c_row,  # type: ignore[arg-type]
            human_input=HumanInput.model_validate({"kind": "proceed"}),
            user_id=user_c_id,
            team_id=team_c_id,
            request=request,  # type: ignore[arg-type]
        )
        assert resumed.status == "running"

    # --- 断言审计：五个动作齐全、payload/target 正确 ---
    async with orm_factory() as session:
        audits_a = await _audit_actions(session, team_id=team_a_id, run_id=run_a_id)
        audits_b = await _audit_actions(session, team_id=team_b_id, run_id=run_b_id)
        audits_c = await _audit_actions(session, team_id=team_c_id, run_id=run_c_id)

    assert set(audits_a) == {"run.pause", "run.cancel"}
    assert set(audits_b) == {"intervene.ask_followup", "intervene.exclude_evidence"}
    assert set(audits_c) == {"run.resume"}

    assert audits_a["run.pause"].payload["reason"] == "m25-暂停"
    assert audits_a["run.cancel"].payload["reason"] == "m25-取消"
    assert audits_a["run.pause"].target_type == "run"
    assert audits_b["intervene.ask_followup"].payload["question"] == "补充追问"
    assert audits_b["intervene.exclude_evidence"].payload["evidence_id"] == evidence_id
    assert audits_c["run.resume"].payload == {"branch": "proceed"}


# ---------------------------------------------------------------------------
# AC-10：(run_id, idempotency_key) 唯一索引与 JSONB 往返
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intervention_idempotency_unique_index_real_db(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同 (run_id, key) 第二条被唯一索引拒绝；NULL 键互不冲突可并存。"""
    from sqlalchemy.exc import IntegrityError

    async with orm_factory() as session:
        run, user, _team = await _seed_chain(session)
        run_id, user_id = run.id, user.id
        await session.commit()

    async with orm_factory() as session:
        base = dict(
            run_id=run_id,
            user_id=user_id,
            type="ask_followup",
            payload={"question": "首条"},
            status="pending",
        )
        session.add(RunIntervention(id=new_ulid(), idempotency_key="dup-key", **base))
        await session.commit()

        session.add(RunIntervention(id=new_ulid(), idempotency_key="dup-key", **base))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # NULL 键不参与唯一比较：两条可并存
        session.add(RunIntervention(id=new_ulid(), idempotency_key=None, **base))
        session.add(RunIntervention(id=new_ulid(), idempotency_key=None, **base))
        await session.commit()

        # JSONB 以 dict 写、以 dict 读回
        from sqlalchemy import select

        all_rows = list(
            (await session.scalars(select(RunIntervention).where(RunIntervention.run_id == run_id))).all()
        )
        assert len(all_rows) == 3
        assert {r.payload["question"] for r in all_rows} == {"首条"}


# ---------------------------------------------------------------------------
# AC-14：0004 迁移 upgrade/downgrade 表与唯一索引
# ---------------------------------------------------------------------------


@pytest.fixture
async def migration_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[str]:
    """准备空 m25_migration schema，并把 DB_SYNC_URL 指向它（search_path 直传）。"""
    dsn = os.environ.get("TEST_DATABASE_URL", _DEFAULT_DSN)
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_MIGRATION_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_MIGRATION_SCHEMA}"))
            await conn.execute(
                text(
                    f"CREATE TABLE {_MIGRATION_SCHEMA}.alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过 0004 迁移集成测试：{exc!r}")

    sync_dsn = (
        dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        + f"?options=-c search_path={_MIGRATION_SCHEMA},public"
    )
    monkeypatch.setenv("DB_SYNC_URL", sync_dsn)
    get_settings.cache_clear()
    try:
        yield sync_dsn
    finally:
        get_settings.cache_clear()
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_MIGRATION_SCHEMA} CASCADE"))
        await engine.dispose()


def _table_exists(sync_conn: Any, table_name: str) -> bool:
    row = sync_conn.execute(
        text(
            """
            SELECT 1 FROM pg_tables
            WHERE schemaname = :schema AND tablename = :table
            """
        ),
        {"schema": _MIGRATION_SCHEMA, "table": table_name},
    ).first()
    return row is not None


def _idempotency_index_unique(sync_conn: Any) -> bool | None:
    """查 0004 幂等唯一索引是否存在及其 indisunique 标记；缺索引返回 None。"""
    row = sync_conn.execute(
        text(
            """
            SELECT i.indisunique
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema
              AND c.relname = 'ix_run_intervention_idempotency'
            """
        ),
        {"schema": _MIGRATION_SCHEMA},
    ).first()
    return None if row is None else bool(row[0])


@pytest.mark.asyncio
async def test_alembic_0004_intervention_table_cycle(migration_dsn: str) -> None:
    """AC-14：0004 表+唯一索引 → downgrade -1 全删 → upgrade 恢复（钉死版本，不随 head 漂移）。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "app" / "db" / "migrations"))
    cfg.set_main_option("path_separator", "os")

    command.upgrade(cfg, "0004")
    engine = create_engine(migration_dsn)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0004"
            assert _table_exists(conn, "run_interventions")
            assert _idempotency_index_unique(conn) is True

        command.downgrade(cfg, "-1")
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0003"
            assert not _table_exists(conn, "run_interventions")
            assert _idempotency_index_unique(conn) is None

        command.upgrade(cfg, "0004")
        with engine.connect() as conn:
            assert _table_exists(conn, "run_interventions")
            assert _idempotency_index_unique(conn) is True
    finally:
        engine.dispose()
