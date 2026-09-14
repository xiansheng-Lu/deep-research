"""M2-8a 埋点接收与指标出数真库集成测试（AC-8~AC-12）。

CI 无 PG 自动跳过；两个独立 schema：
- ``m28_orm``：事件落库 + A8 三指标聚合（成功率/介入率/溯源率/意图降级率）；
- ``m28_migration``：alembic head(0006) telemetry_events 表/索引 → downgrade -1
  回 0005 消失 → upgrade 恢复。
跑后两 schema DROP CASCADE 无残留。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import create_all_in_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 注册全部表
    AuditEntry,
    Evidence,
    Project,
    Report,
    ReportCitation,
    ResearchRun,
    RunIntervention,
    SubQuestion,
    Team,
    TelemetryEvent,
    User,
)
from app.services import telemetry as telemetry_service

pytestmark = pytest.mark.integration

_ORM_SCHEMA = "m28_orm"
_MIGRATION_SCHEMA = "m28_migration"
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"
_BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# m28_orm
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
        pytest.skip(f"测试库不可达，跳过 M2-8a ORM 集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_base(session: AsyncSession) -> tuple[str, str, str, datetime]:
    now = datetime.now(tz=UTC)
    team = Team(id=new_ulid(), name="M28团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="M28用户",
        role="owner",
    )
    project = Project(id=new_ulid(), team_id=team.id, owner_id=user.id, name="M28项目", status="active")
    session.add_all([team, user, project])
    await session.commit()
    return user.id, team.id, project.id, now


def _run(
    *, run_id: str, project_id: str, user_id: str, status: str, now: datetime, started: bool
) -> ResearchRun:
    return ResearchRun(
        id=run_id,
        project_id=project_id,
        creator_id=user_id,
        template_id="generic",
        tier="standard",
        question="指标聚合测试问题",
        status=status,
        token_budget=100_000,
        token_used=10,
        started_at=now if started else None,
        finished_at=now if status in ("succeeded", "failed", "cancelled") else None,
    )


async def test_ingest_and_metrics_aggregation(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC-1/8/9/10/11：事件落库 + 三指标窗口聚合口径正确。"""
    async with orm_factory() as session:
        user_id, team_id, project_id, now = await _seed_base(session)

        # 4 个终态 run：2 succeeded / 1 failed / 1 cancelled（另有 1 个 running 不计）
        runs = [
            _run(
                run_id="r-suc1",
                project_id=project_id,
                user_id=user_id,
                status="succeeded",
                now=now,
                started=True,
            ),
            _run(
                run_id="r-suc2",
                project_id=project_id,
                user_id=user_id,
                status="succeeded",
                now=now,
                started=True,
            ),
            _run(
                run_id="r-fail",
                project_id=project_id,
                user_id=user_id,
                status="failed",
                now=now,
                started=True,
            ),
            _run(
                run_id="r-cancel",
                project_id=project_id,
                user_id=user_id,
                status="cancelled",
                now=now,
                started=True,
            ),
            _run(
                run_id="r-run",
                project_id=project_id,
                user_id=user_id,
                status="running",
                now=now,
                started=True,
            ),
        ]
        session.add_all(runs)
        # 先提交外键父链（runs），UOW 不保证 interventions/audit 与其同批顺序
        await session.commit()

        # r-suc1 上挂 1 个 exclude 介入（applied）+ 1 条 pause 审计
        session.add(
            RunIntervention(
                id=new_ulid(),
                run_id="r-suc1",
                user_id=user_id,
                type="exclude_evidence",
                payload={},
                status="applied",
            )
        )
        session.add(
            AuditEntry(
                id=new_ulid(),
                team_id=team_id,
                user_id=user_id,
                action="run.pause",
                target_type="run",
                target_id="r-suc2",
                payload={},
                trace_id=None,
                created_at=now,
            )
        )

        # 2 个 final 报告（挂在成功 run 上），机器绑定率分别 1.0/0.5
        for rid, rate in [("r-suc1", 1.0), ("r-suc2", 0.5)]:
            session.add(
                Report(
                    id=new_ulid(),
                    run_id=rid,
                    template_id="generic",
                    status="final",
                    content_md="md",
                    content_json={
                        "outline": [],
                        "blocks": [],
                        "citation_audit": {"numeric_claim_binding_rate": rate},
                    },
                    token_used=10,
                )
            )
        await session.commit()

        # 埋点事件：citation.open 落在 r-suc1（3 次，distinct run 仍 1）；
        # intent.classify 4 次其中 1 次 degraded
        def evt(event: str, run_id: str | None, props: dict[str, Any]) -> TelemetryEvent:
            return TelemetryEvent(
                id=new_ulid(),
                user_id=user_id,
                team_id=team_id,
                event=event,
                run_id=run_id,
                page="/r",
                props=props,
                event_ts=now,
                created_at=now,
            )

        session.add_all(
            [
                evt("report.citation.open", "r-suc1", {"evidence_id": "e1"}),
                evt("report.citation.open", "r-suc1", {"evidence_id": "e2"}),
                evt("report.citation.open", "r-suc2", {"evidence_id": "e3"}),
                evt("report.citation.open", "r-unknown", {"evidence_id": "e9"}),  # 无 final 报告，去伪剔除
                evt("intent.classify", None, {"degraded": False}),
                evt("intent.classify", None, {"degraded": False}),
                evt("intent.classify", None, {"degraded": False}),
                evt("intent.classify", None, {"degraded": True}),
                # clarify 成功事件（观测口径，by_action）
                evt("cockpit.intervene", "r-suc1", {"action": "clarify", "result": "success"}),
            ]
        )
        await session.commit()

    # 独立会话聚合
    async with orm_factory() as ro:
        frm = now - timedelta(days=1)
        to = now + timedelta(days=1)
        metrics = await telemetry_service.get_metrics(ro, frm=frm, to=to)

        # 成功率：2/(2+1)，cancelled/running 排除
        assert metrics.runs.total_terminal == 4
        assert metrics.runs.succeeded == 2
        assert metrics.runs.failed == 1
        assert metrics.runs.cancelled == 1
        assert metrics.runs.success_rate == round(2 / 3, 3)

        # 介入率：5 started（含 running，看到看板即计），r-suc1(exclude)+r-suc2(pause)=2
        assert metrics.intervention.runs_started == 5
        assert metrics.intervention.runs_with_intervention == 2
        assert metrics.intervention.intervention_rate == 0.4
        assert metrics.intervention.by_action["pause"] == 1
        assert metrics.intervention.by_action["exclude"] == 1
        assert metrics.intervention.by_action["clarify"] == 1
        assert metrics.intervention.by_action["resume"] == 0

        # 溯源率：2 final，均值 0.75；交互口径 distinct run（r-unknown 被去伪）=2/2
        assert metrics.traceability.final_reports == 2
        assert metrics.traceability.avg_numeric_binding_rate == 0.75
        assert metrics.traceability.runs_with_citation_open == 2
        assert metrics.traceability.citation_open_rate == 1.0
        assert metrics.traceability.citation_open_events == 4

        # 意图降级：1/4
        assert metrics.intent.classify_total == 4
        assert metrics.intent.degraded == 1
        assert metrics.intent.degraded_rate == 0.25


async def test_empty_window_returns_null_rates(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC-8 无样本时率字段为 null，不返回 0。"""
    async with orm_factory() as session:
        await _seed_base(session)
    async with orm_factory() as ro:
        now = datetime.now(tz=UTC)
        metrics = await telemetry_service.get_metrics(
            ro, frm=now - timedelta(days=1), to=now + timedelta(days=1)
        )
        assert metrics.runs.success_rate is None
        assert metrics.intervention.intervention_rate is None
        assert metrics.traceability.citation_open_rate is None
        assert metrics.intent.degraded_rate is None


def test_parse_window_validation() -> None:
    """AC-11：窗口默认值/越界/倒置校验（纯函数，无需 DB）。"""
    now = datetime.now(tz=UTC)
    frm, to = telemetry_service.parse_window(None, None)
    assert (to - frm) == timedelta(days=30)

    with pytest.raises(ValueError, match="from 必须早于 to"):
        telemetry_service.parse_window(to, now - timedelta(days=1))
    with pytest.raises(ValueError, match="90 天"):
        telemetry_service.parse_window(now - timedelta(days=91), now + timedelta(days=1))


# ---------------------------------------------------------------------------
# m28_migration：0006 升级/回滚循环
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
        pytest.skip(f"测试库不可达，跳过 0006 迁移集成测试：{exc!r}")

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


def _table_exists(sync_conn: Any, table: str) -> bool:
    row = sync_conn.execute(
        text("SELECT 1 FROM pg_tables WHERE schemaname=:s AND tablename=:t"),
        {"s": _MIGRATION_SCHEMA, "t": table},
    ).first()
    return row is not None


def _index_exists(sync_conn: Any, index: str) -> bool:
    row = sync_conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE schemaname=:s AND indexname=:i"),
        {"s": _MIGRATION_SCHEMA, "i": index},
    ).first()
    return row is not None


@pytest.mark.asyncio
async def test_alembic_0006_telemetry_table_cycle(migration_dsn: str) -> None:
    """AC-12：head(0006) 表+四索引 → downgrade -1 回 0005 消失 → upgrade 恢复。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "app" / "db" / "migrations"))
    cfg.set_main_option("path_separator", "os")

    command.upgrade(cfg, "head")
    engine = create_engine(migration_dsn)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0006"
            assert _table_exists(conn, "telemetry_events")
            for index in (
                "ix_telemetry_events_created_at",
                "ix_telemetry_events_event_time",
                "ix_telemetry_events_run",
                "ix_telemetry_events_user_time",
            ):
                assert _index_exists(conn, index), index

        command.downgrade(cfg, "-1")
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0005"
            assert not _table_exists(conn, "telemetry_events")

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0006"
            assert _table_exists(conn, "telemetry_events")
    finally:
        engine.dispose()
