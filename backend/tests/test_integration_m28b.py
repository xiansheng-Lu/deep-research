"""M2-8b 集成测试：0007 租约观测列迁移（B-AC-10）与孤儿清扫真库扫描/收敛（B-AC-8）。

- ``m28b_orm``：真库造新鲜/失联 run，验证 reaper 扫描 SQL 的租约新鲜度过滤、
  无报告孤儿收敛 failed(RUN_WORKER_LOST)、pending 重投一次后超限收敛、审计落库；
- ``m28b_migration``：alembic 0007 租约观测列/索引 upgrade/downgrade 往返。

真库不可达时自动 skip（与既有 m24/m28 迁移测试同策略）。跑后 schema DROP 无残留。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import fakeredis.aioredis
import pytest
from alembic import command
from alembic.config import Config
from conftest import create_all_in_schema
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 注册全部表
    AuditEntry,
    Project,
    ResearchRun,
    Team,
    User,
)
from app.orchestrator.lease import RunLease
from app.orchestrator.registry import RedisRunRegistry
from app.realtime.hub import RealtimeHub
from app.workers import reaper as reaper_module
from app.workers.bootstrap import WorkerDeps
from app.workers.tasks import research as research_tasks

pytestmark = pytest.mark.integration

_ORM_SCHEMA = "m28b_orm"
_MIGRATION_SCHEMA = "m28b_migration"
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"


# ---------------------------------------------------------------------------
# m28b_orm：孤儿清扫扫描 SQL 与真实收敛
# ---------------------------------------------------------------------------


@pytest.fixture
async def orm_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    dsn = os.environ.get("TEST_DATABASE_URL", _DEFAULT_DSN)
    engine = create_async_engine(
        dsn,
        pool_size=3,
        connect_args={"server_settings": {"search_path": f"{_ORM_SCHEMA},public"}},
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_ORM_SCHEMA}"))
        async with engine.begin() as conn:
            await conn.run_sync(create_all_in_schema, _ORM_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过 M2-8b ORM 集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_project(session: AsyncSession) -> tuple[str, str]:
    team = Team(id=new_ulid(), name="M28B团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="M28B用户",
        role="owner",
    )
    project = Project(id=new_ulid(), team_id=team.id, owner_id=user.id, name="M28B项目", status="active")
    session.add_all([team, user, project])
    await session.commit()
    return user.id, project.id


def _reaper_deps(
    maker: async_sessionmaker[AsyncSession],
    redis_client: Any,
) -> WorkerDeps:
    lease = RunLease(redis_client, worker_id="worker-reaper-test", ttl_seconds=30)
    return WorkerDeps(
        settings=get_settings(),
        session_factory=maker,
        checkpointer=None,
        hub=RealtimeHub(),
        redis=redis_client,
        lease=lease,
        registry=RedisRunRegistry(lease),
        worker_id="worker-reaper-test",
        llm=None,
        retrieval_client=None,
    )


async def test_reaper_sweep_filters_fresh_lease_and_converges_orphans(
    orm_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-AC-8 真库：新鲜租约不扫；失联 running 无报告 → failed(RUN_WORKER_LOST)；
    pending 未启动 → 重投一次，二次清扫超上限 → failed；收敛写审计与终态列。"""
    now = datetime.now(tz=UTC)
    old = now - timedelta(minutes=5)
    async with orm_factory() as session:
        user_id, project_id = await _seed_project(session)
        fresh = ResearchRun(
            id="r-fresh",
            project_id=project_id,
            creator_id=user_id,
            template_id="generic",
            tier="standard",
            question="新鲜租约 run",
            status="running",
            current_stage="retrieve",
            token_budget=1000,
            token_used=10,
            started_at=old,
            created_at=old,
            updated_at=old,
            execution_owner="worker-live",
            lease_until=now + timedelta(seconds=30),
        )
        orphan = ResearchRun(
            id="r-orphan",
            project_id=project_id,
            creator_id=user_id,
            template_id="generic",
            tier="standard",
            question="失联 run",
            status="running",
            current_stage="retrieve",
            token_budget=1000,
            token_used=88,
            started_at=old,
            created_at=old,
            updated_at=old,
            execution_owner="worker-dead",
            lease_until=None,
        )
        pending = ResearchRun(
            id="r-pending",
            project_id=project_id,
            creator_id=user_id,
            template_id="generic",
            tier="standard",
            question="从未启动 run",
            status="pending",
            current_stage="clarify",
            token_budget=1000,
            token_used=0,
            started_at=None,
            created_at=old,
            updated_at=old,
        )
        session.add_all([fresh, orphan, pending])
        await session.commit()

    # pending 重投不得真连 broker：拦截 Celery 投递
    delay = MagicMock()
    monkeypatch.setattr(research_tasks.execute_run, "delay", delay)

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    deps = _reaper_deps(orm_factory, redis_client)

    # 第一次清扫：孤儿 failed、pending 重投、新鲜 run 不动
    first = await reaper_module.reap_orphans(deps)
    assert first.scanned == 2
    assert first.failed == 1 and first.redelivered == 1
    assert first.paused == 0 and first.succeeded == 0 and first.cancelled == 0
    delay.assert_called_once_with("r-pending")
    assert int(await redis_client.get("reap:retry:r-pending")) == 1

    async with orm_factory() as session:
        fresh_row = await session.get(ResearchRun, "r-fresh")
        orphan_row = await session.get(ResearchRun, "r-orphan")
        pending_row = await session.get(ResearchRun, "r-pending")
        assert fresh_row is not None and fresh_row.status == "running"
        assert orphan_row is not None
        assert orphan_row.status == "failed"
        assert orphan_row.error_code == "RUN_WORKER_LOST"
        assert orphan_row.finished_at is not None
        assert pending_row is not None and pending_row.status == "pending"
        # 孤儿收敛写 run.orphan_recovered 审计（scalar 取实体，存在即一行）
        audit_row = await session.scalar(
            select(AuditEntry).where(
                AuditEntry.target_id == "r-orphan",
                AuditEntry.action == "run.orphan_recovered",
            )
        )
        assert audit_row is not None

    # 第二次清扫：重投计数超限，pending 收敛 failed；fresh 仍被租约新鲜度过滤
    second = await reaper_module.reap_orphans(deps)
    assert second.scanned == 1 and second.failed == 1 and second.redelivered == 0
    async with orm_factory() as session:
        pending_row = await session.get(ResearchRun, "r-pending")
        assert pending_row is not None
        assert pending_row.status == "failed"
        assert pending_row.error_code == "RUN_WORKER_LOST"
        assert pending_row.finished_at is not None

    await redis_client.aclose()
    await deps.hub.aclose()


# ---------------------------------------------------------------------------
# m28b_migration：0007 租约观测列迁移循环
# ---------------------------------------------------------------------------


@pytest.fixture
async def migration_dsn(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
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
        pytest.skip(f"测试库不可达，跳过 0007 迁移集成测试：{exc!r}")

    sync_dsn = (
        dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        + f"?options=-c search_path={_MIGRATION_SCHEMA},public"
    )
    monkeypatch.setenv("DB_SYNC_URL", sync_dsn)
    try:
        yield sync_dsn
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_MIGRATION_SCHEMA} CASCADE"))
        await engine.dispose()


def _alembic_cfg() -> Config:
    cfg = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_DIR, "app", "db", "migrations"))
    cfg.set_main_option("path_separator", "os")
    return cfg


def _column_nullable(sync_conn: Any, column: str) -> bool | None:
    row = sync_conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema=:s AND table_name='research_runs' AND column_name=:c"
        ),
        {"s": _MIGRATION_SCHEMA, "c": column},
    ).first()
    return None if row is None else row[0] == "YES"


def _index_exists(sync_conn: Any, index: str) -> bool:
    row = sync_conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE schemaname=:s AND indexname=:i"),
        {"s": _MIGRATION_SCHEMA, "i": index},
    ).first()
    return row is not None


def test_alembic_0007_lease_columns_cycle(migration_dsn: str) -> None:
    """B-AC-10：0007 两列可空 + 复合索引；downgrade 后列与索引消失；upgrade 往返。"""
    cfg = _alembic_cfg()
    engine = create_engine(migration_dsn)
    try:
        command.upgrade(cfg, "0007")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"
            assert _column_nullable(conn, "execution_owner") is True
            assert _column_nullable(conn, "lease_until") is True
            assert _index_exists(conn, "ix_runs_status_lease") is True

        command.downgrade(cfg, "0006")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
            assert _column_nullable(conn, "execution_owner") is None
            assert _column_nullable(conn, "lease_until") is None
            assert _index_exists(conn, "ix_runs_status_lease") is False

        command.upgrade(cfg, "0007")
        with engine.connect() as conn:
            assert _column_nullable(conn, "execution_owner") is True
            assert _index_exists(conn, "ix_runs_status_lease") is True
    finally:
        engine.dispose()
