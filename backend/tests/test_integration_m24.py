"""M2-3/M2-4 真实 Postgres 集成测试（AC-1 / AC-12 / AC-15）。

CI 无外部数据库时自动跳过；本地以 WSL2 Docker 内 Postgres 为目标：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research

隔离 schema 一律使用 ``m23_`` 前缀（``m23_orm`` 走 ORM 建表，
``m23_migration`` 走 alembic 迁移建表），search_path 追加 public 解析
pgvector 类型；setup 重建、teardown DROP CASCADE。

覆盖：
- AC-1：ensure_stage_rows 真库六行/幂等、(run_id,name) 唯一约束拒绝重复；
- AC-12：执行器每 super-step 中间提交，独立会话在 run 运行中即可读到
  阶段行、子问题、证据与成本快照；
- AC-15：0003 迁移 head→downgrade -1→upgrade 全程成功，约束随迁移增减。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from conftest import create_all_in_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_executor import _BudgetedStructuredLLM, _OfflineRetrievalClient

from app.core.config import get_settings
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 导入以注册表到 Base.metadata
    Project,
    ResearchRun,
    Team,
    User,
)
from app.orchestrator.executor import run_research_async
from app.orchestrator.persistence import STAGE_ORDER, ensure_stage_rows
from app.realtime.hub import RealtimeHub
from app.services import dashboard

pytestmark = pytest.mark.integration

_ORM_SCHEMA = "m23_orm"
_MIGRATION_SCHEMA = "m23_migration"
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
    """m23_orm schema 的 ORM 会话工厂（建表走 metadata，不经迁移）。"""
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
        pytest.skip(f"测试库不可达，跳过 M2-3/M2-4 集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_run(
    session: AsyncSession, *, tier: str = "quick", token_budget: int = 100_000
) -> tuple[str, str, str]:
    """插入 Team→User→Project→ResearchRun 最小外键链，返回 (run_id,user_id,team_id)。"""
    team = Team(id=new_ulid(), name="M23集成团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="M23集成用户",
        role="owner",
    )
    project = Project(
        id=new_ulid(),
        team_id=team.id,
        owner_id=user.id,
        name="M23集成项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier=tier,
        question="中间提交可见性集成测试问题",
        status="pending",
        token_budget=token_budget,
    )
    session.add_all([team, user, project, run])
    await session.commit()
    return run.id, user.id, team.id


# ---------------------------------------------------------------------------
# AC-1：阶段行真库语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_stage_rows_real_db_idempotent(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """真库：首次六行 pending；二次调用不新增；行状态可独立会话读回。"""
    async with orm_factory() as session:
        run_id, _user_id, _team_id = await _seed_run(session)

    async with orm_factory() as session:
        rows = await ensure_stage_rows(session, run_id=run_id)
        await session.commit()
    assert list(rows.keys()) == list(STAGE_ORDER)

    # 独立会话读回：恰好六行、全 pending/attempt=1、时间戳齐全
    async with orm_factory() as session:
        again = await ensure_stage_rows(session, run_id=run_id)
        assert len(again) == 6
        assert all(r.status == "pending" and r.attempt == 1 for r in again.values())
        assert all(r.started_at is None and r.finished_at is None for r in again.values())
        from sqlalchemy import func, select

        from app.db.models.run import Stage

        count = await session.scalar(select(func.count()).select_from(Stage).where(Stage.run_id == run_id))
        assert count == 6


@pytest.mark.asyncio
async def test_stage_run_name_unique_constraint_rejects_duplicate(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC-1：同 (run_id,name) 第二行被 uq_stages_run_name 拒绝；异名行正常。"""
    import pytest as _pytest
    from sqlalchemy.exc import IntegrityError

    from app.db.models.run import Stage

    async with orm_factory() as session:
        run_id, _user_id, _team_id = await _seed_run(session)
        rows = await ensure_stage_rows(session, run_id=run_id)
        await session.commit()
        assert len(rows) == 6

        duplicate = Stage(id=new_ulid(), run_id=run_id, name="clarify", status="pending", attempt=1)
        session.add(duplicate)
        with _pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # 回滚后链路上原有六行仍可正常查询（约束没有误伤数据）
        from sqlalchemy import func, select

        count = await session.scalar(select(func.count()).select_from(Stage).where(Stage.run_id == run_id))
        assert count == 6


# ---------------------------------------------------------------------------
# AC-12：执行中中间提交对独立会话可见
# ---------------------------------------------------------------------------


async def _poll_until(coro_factory, predicate, wait_seconds: float = 10.0) -> object:
    """轮询独立会话只读查询，直到 predicate 成立（帧先于提交发出，需重试读）。"""
    import asyncio

    deadline = asyncio.get_event_loop().time() + wait_seconds
    last: object = None
    while True:
        last = await coro_factory()
        if predicate(last):
            return last
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"轮询超时，最后一次结果：{last!r}")
        await asyncio.sleep(0.002)


@pytest.mark.asyncio
async def test_intermediate_commits_visible_to_independent_session(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """run 运行中：独立会话先后读到阶段推进、子问题、证据、非零成本快照。"""
    import asyncio

    async with orm_factory() as session:
        run_id, user_id, team_id = await _seed_run(session, token_budget=100_000)
        run = await session.get(ResearchRun, run_id)
        project_id = run.project_id

    hub = RealtimeHub()
    events: asyncio.Queue[dict] = asyncio.Queue()

    async def _pump() -> None:
        async for event in hub.subscribe(f"runs:{run_id}"):
            await events.put(event)

    pump_task = asyncio.create_task(_pump())
    run_task = asyncio.create_task(
        run_research_async(
            run_id=run_id,
            project_id=project_id,
            template_id="generic",
            tier="quick",
            question="中间提交可见性集成测试问题",
            token_budget=100_000,
            clarification=None,
            team_id=team_id,
            creator_id=user_id,
            trace_id="tr-m23",
            session_factory=orm_factory,
            llm=_BudgetedStructuredLLM(tokens_per_call=60),
            retrieval_client=_OfflineRetrievalClient(),
            hub=hub,
        )
    )

    async def _wait_event(event_type: str) -> dict:
        while True:
            event = await asyncio.wait_for(events.get(), timeout=15.0)
            if event["type"] == event_type:
                return event

    async def _read_stage_statuses() -> dict[str, str]:
        async with orm_factory() as ro:
            rows = await dashboard.list_run_stages(ro, run_id=run_id, user_id=user_id)
            return {r.name: r.status for r in rows}

    async def _read_counts() -> tuple[int, int, int, str]:
        async with orm_factory() as ro:
            sqs = await dashboard.list_run_sub_questions(ro, run_id=run_id, user_id=user_id)
            _evs, total_ev = await dashboard.list_run_evidence(
                ro, run_id=run_id, user_id=user_id, page_size=100
            )
            run_row, _ratio = await dashboard.get_cost_snapshot(ro, run_id=run_id, user_id=user_id)
            return len(sqs), total_ev, int(run_row.token_used), run_row.status

    try:
        # 1) retrieve 已开始：上一阶段 decompose 必须已 succeeded 且子问题可读
        await _wait_event("stage.started")  # clarify
        await _wait_event("stage.started")  # decompose
        await _wait_event("stage.started")  # retrieve
        statuses = await _poll_until(
            _read_stage_statuses,
            lambda m: m.get("decompose") == "succeeded" and m.get("retrieve") == "running",
        )
        assert statuses["clarify"] == "succeeded"
        _sq_count, _ev_total, used_mid, run_status = await _poll_until(_read_counts, lambda r: r[0] >= 1)
        # 两次结构化调用已落账：运行中即可读到非零成本
        assert used_mid >= 60
        assert run_status == "running"

        # 2) critique 已开始：standardize 落库的证据在终态提交前就可读
        await _wait_event("stage.started")  # standardize
        await _wait_event("stage.started")  # critique
        _sq_count, ev_total, _used, run_status = await _poll_until(_read_counts, lambda r: r[1] >= 1)
        assert ev_total >= 1
        assert run_status == "running"

        # 3) 终态
        finished = await _wait_event("run.finished")
        assert finished["status"] == "succeeded"
        await run_task
    finally:
        if not run_task.done():
            run_task.cancel()
        # return_exceptions 吞掉 CancelledError，保证订阅异步生成器被正常收尾
        pump_task.cancel()
        await asyncio.gather(run_task, pump_task, return_exceptions=True)

    async with orm_factory() as session:
        run = await session.get(ResearchRun, run_id)
        assert run.status == "succeeded"


@pytest.fixture
async def migration_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[str, str]]:
    """准备空 m23_migration schema，并把 DB_SYNC_URL 指向它（search_path 直传）。

    yields (async_dsn, sync_dsn)；库不可达时 skip。
    """
    dsn = os.environ.get("TEST_DATABASE_URL", _DEFAULT_DSN)
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_MIGRATION_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_MIGRATION_SCHEMA}"))
            # 预建空版本表：防止 search_path 回退到 public 时误用其中残留的
            # alembic_version（开发库 public 可能存在历史版本表）
            await conn.execute(
                text(
                    f"CREATE TABLE {_MIGRATION_SCHEMA}.alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过 0003 迁移集成测试：{exc!r}")

    # 直连参数里带空格（不经百分号编码），避开 alembic ConfigParser 的 % 插值；
    # public 必须保留以解析 pgvector 的 vector 类型
    sync_dsn = (
        dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        + f"?options=-c search_path={_MIGRATION_SCHEMA},public"
    )
    monkeypatch.setenv("DB_SYNC_URL", sync_dsn)
    get_settings.cache_clear()
    try:
        yield dsn, sync_dsn
    finally:
        get_settings.cache_clear()
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_MIGRATION_SCHEMA} CASCADE"))
        await engine.dispose()


# ---------------------------------------------------------------------------
# AC-15：0003 迁移 upgrade/downgrade
# ---------------------------------------------------------------------------


def _constraint_exists(sync_conn: object, schema: str) -> bool:
    row = sync_conn.execute(  # type: ignore[attr-defined]
        text(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE n.nspname = :schema
              AND t.relname = 'stages'
              AND c.conname = 'uq_stages_run_name'
              AND c.contype = 'u'
            """
        ),
        {"schema": schema},
    ).first()
    return row is not None


def _alembic_version(sync_conn: object) -> str | None:
    row = sync_conn.execute(text("SELECT version_num FROM alembic_version")).first()  # type: ignore[attr-defined]
    return str(row[0]) if row else None


@pytest.mark.asyncio
async def test_alembic_0003_upgrade_downgrade_cycle(
    migration_dsn: tuple[str, str],
) -> None:
    """AC-15：head→downgrade -1→upgrade 退出成功，唯一约束随迁移增减。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    _async_dsn, sync_dsn = migration_dsn

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "app" / "db" / "migrations"))
    # alembic 1.13 起要求显式 path_separator，否则抛 DeprecationWarning
    # （本仓 pytest 将告警升级为错误）
    cfg.set_main_option("path_separator", "os")

    command.upgrade(cfg, "head")
    engine = create_engine(sync_dsn)
    try:
        with engine.connect() as conn:
            assert _alembic_version(conn) == "0003"
            assert _constraint_exists(conn, _MIGRATION_SCHEMA)

        command.downgrade(cfg, "-1")
        with engine.connect() as conn:
            assert _alembic_version(conn) == "0002"
            assert not _constraint_exists(conn, _MIGRATION_SCHEMA)

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert _alembic_version(conn) == "0003"
            assert _constraint_exists(conn, _MIGRATION_SCHEMA)

        # 约束真实生效：插入外键链 + 两条同名阶段行，第二条被拒
        with Session(engine) as orm_session:
            team = Team(id=new_ulid(), name="迁移验证团队", plan="free", settings={})
            user = User(
                id=new_ulid(),
                team_id=team.id,
                email=f"{new_ulid().lower()}@example.com",
                hashed_password="x",
                display_name="迁移验证用户",
                role="owner",
            )
            project = Project(
                id=new_ulid(),
                team_id=team.id,
                owner_id=user.id,
                name="迁移验证项目",
                status="active",
            )
            run = ResearchRun(
                id=new_ulid(),
                project_id=project.id,
                creator_id=user.id,
                template_id="generic",
                tier="quick",
                question="迁移验证问题",
                token_budget=1000,
            )
            orm_session.add_all([team, user, project, run])
            orm_session.flush()
            from app.db.models.run import Stage

            orm_session.add(
                Stage(
                    id=new_ulid(),
                    run_id=run.id,
                    name="clarify",
                    status="succeeded",
                    attempt=1,
                )
            )
            orm_session.flush()
            orm_session.add(
                Stage(
                    id=new_ulid(),
                    run_id=run.id,
                    name="clarify",
                    status="pending",
                    attempt=1,
                )
            )
            with pytest.raises(IntegrityError):
                orm_session.flush()
            orm_session.rollback()
    finally:
        engine.dispose()
