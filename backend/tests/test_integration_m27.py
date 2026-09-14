"""M2-7 数据点级溯源真库集成测试（AC-11）。

CI 无外部数据库时自动跳过；两个独立 schema：
- ``m27_orm``：ORM 建表，blocks 引擎装配 → final Report + report_citations 展开
  落库，独立会话读回并走 service 层验证信源索引（去重/排序/九展示字段）；
- ``m27_migration``：alembic upgrade head(0005) 校验列/约束/索引，downgrade -1
  回 0004 后消失，再 upgrade 恢复。

两个 schema 用后即 DROP CASCADE，无残留。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import create_all_in_schema
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 注册全部表
    Evidence,
    Project,
    Report,
    ReportCitation,
    ResearchRun,
    SubQuestion,
    Team,
    User,
)
from app.reporting.blocks import DraftBlock, EvidenceText, assemble_report
from app.services import reports as reports_service

pytestmark = pytest.mark.integration

_ORM_SCHEMA = "m27_orm"
_MIGRATION_SCHEMA = "m27_migration"
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"
_BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# m27_orm：blocks → 引文落库 → 独立会话读回 / service 信源索引
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
    except Exception as exc:  # noqa: BLE001 - CI 无 PG 时整体跳过
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过 M2-7 ORM 集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed(
    session: AsyncSession,
) -> tuple[str, str, list[Evidence]]:
    """插链并造 2 条证据；返回 (run_id, user_id, evidence_rows)。"""
    now = datetime.now(tz=UTC)
    team = Team(id=new_ulid(), name="M27团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="M27用户",
        role="owner",
    )
    project = Project(id=new_ulid(), team_id=team.id, owner_id=user.id, name="M27项目", status="active")
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="standard",
        question="2026 年新能源汽车销量趋势",
        status="succeeded",
        token_budget=100_000,
    )
    subq = SubQuestion(
        id=new_ulid(),
        run_id=run.id,
        question="销量趋势",
        depends_on=[],
        status="succeeded",
        evidence_count=2,
    )
    ev1 = Evidence(
        id=new_ulid(),
        run_id=run.id,
        sub_question_id=subq.id,
        url="https://stats.gov.cn/tjgb/2026",
        domain="stats.gov.cn",
        title="统计局年度公报",
        snippet="统计局公报摘要",
        content="背景说明。2026 年新能源汽车销量同比增长 30%，出口保持高位。尾部说明。",
        source_type="official_doc",
        source_level="primary",
        credibility="A",
        relevance_score=0.86,
        fingerprint="fp-m27-1",
        published_at=None,
        fetched_at=now,
    )
    ev2 = Evidence(
        id=new_ulid(),
        run_id=run.id,
        sub_question_id=subq.id,
        url="https://zhihu.com/q/1",
        domain="zhihu.com",
        title="社区讨论帖",
        snippet="社区观点摘要",
        content=None,
        source_type="community",
        source_level="tertiary",
        credibility="C",
        relevance_score=0.4,
        fingerprint="fp-m27-2",
        published_at=None,
        fetched_at=now,
    )
    # 先提交外键父链（sub_questions 与 evidence 间无 relationship，UOW 不保证同批顺序）
    session.add_all([team, user, project, run, subq])
    await session.commit()
    session.add_all([ev1, ev2])
    await session.commit()
    return run.id, user.id, [ev1, ev2]


def _material_from(rows: list[Evidence]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "sub_question_id": row.sub_question_id,
            "url": row.url,
            "domain": row.domain,
            "title": row.title,
            "snippet": row.snippet,
            "source_type": row.source_type,
            "source_level": row.source_level,
            "credibility": row.credibility,
            "fingerprint": row.fingerprint,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "fetched_at": row.fetched_at.isoformat(),
            "relevance_score": float(row.relevance_score),
        }
        for row in rows
    ]


async def test_blocks_to_citations_roundtrip_and_service_index(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """blocks 装配 → final Report + 引文行落库 → 独立会话读回/信源索引九字段。"""
    async with orm_factory() as session:
        run_id, user_id, evidence_rows = await _seed(session)
        ev1, ev2 = evidence_rows
        material = _material_from(evidence_rows)
        content_map = {
            ev1.id: EvidenceText(snippet=ev1.snippet, content=ev1.content),
            ev2.id: EvidenceText(snippet=ev2.snippet, content=ev2.content),
        }
        drafts = [
            # quote 命中 ev1 正文原句 → 片段应含 30% 原句
            DraftBlock(
                type="conclusion",
                text="官方数据显示销量同比增长，社区讨论提供旁证",
                confidence="cross_verified",
                evidence_ids=[ev1.id, ev2.id],
                quotes={ev1.id: "新能源汽车销量同比增长 30%"},
            ),
            DraftBlock(
                type="evidence",
                text="统计局公报原文展开",
                confidence="cross_verified",
                evidence_ids=[ev1.id],
            ),
            DraftBlock(type="limitation", text="社区来源代表性有限", confidence="inferred"),
        ]
        assembly = assemble_report(
            drafts=drafts,
            material=material,  # type: ignore[arg-type]
            claims=[],
            conflicts=[],
            verdicts=[],
            content_map=content_map,
            question="2026 年新能源汽车销量趋势",
        )
        # 终稿恒等式 + quote 命中
        audit = assembly.audit
        assert audit["claim_blocks"] == audit["bound_blocks"] + audit["forced_inferred_blocks"]
        assert audit["numeric_claim_binding_rate"] == 1.0
        assert audit["quote_verified_refs"] >= 1
        first_block = assembly.blocks[0]
        assert "30%" in first_block["citations"][0]["snippet"]
        assert [c["marker"] for c in first_block["citations"]] == ["[1]", "[2]"]

        report = Report(
            id=new_ulid(),
            run_id=run_id,
            template_id="generic",
            status="final",
            content_md="markdown 留档",
            content_json={
                "outline": assembly.outline,
                "blocks": assembly.blocks,
                "citation_audit": {**audit, "reporter_degraded": False},
            },
            token_used=0,
        )
        session.add(report)
        await session.flush()
        for row in assembly.citation_rows:
            session.add(
                ReportCitation(
                    id=new_ulid(),
                    report_id=report.id,
                    block_id=row["block_id"],
                    evidence_id=row["evidence_id"],
                    claim_id=row.get("claim_id"),
                    position=row["position"],
                    snippet=row["snippet"],
                )
            )
        await session.commit()

    # 独立会话读回：ORM 行、service 响应超集、信源索引
    async with orm_factory() as ro:
        report_row = await reports_service.get_report_for_run(ro, run_id=run_id, user_id=user_id)
        assert report_row.status == "final"
        response = reports_service.to_response(report_row)
        assert response.blocks
        assert {s.id for s in response.outline} >= {
            "sec-overview",
            "sec-findings",
            "sec-limitations",
        }

        items = await reports_service.list_citations(ro, run_id=run_id, user_id=user_id)
        assert [item.marker for item in items] == ["[1]", "[2]"]
        gov = items[0]
        assert gov.evidence_id == ev1.id
        assert gov.url == "https://stats.gov.cn/tjgb/2026"
        assert gov.domain == "stats.gov.cn"
        assert gov.source_type == "official_doc"
        assert gov.credibility == "A"
        assert gov.published_at is None
        assert "30%" in gov.snippet

        citation_rows = (
            await ro.scalars(
                # 块×证据粒度：总行数等于 assembly 展开的 block×证据 条数
                select(ReportCitation).where(ReportCitation.report_id == report_row.id)
            )
        ).all()
        assert len(citation_rows) == len(assembly.citation_rows)
        # 0005：evidence/limitation 块 claim_id 可空
        assert any(row.claim_id is None for row in citation_rows)


# ---------------------------------------------------------------------------
# m27_migration：0005 升级/回滚循环
# ---------------------------------------------------------------------------


@pytest.fixture
async def migration_dsn(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """准备空 m27_migration schema，DB_SYNC_URL 指向它（search_path 直传）。"""
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
        pytest.skip(f"测试库不可达，跳过 0005 迁移集成测试：{exc!r}")

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


def _column_exists(sync_conn: Any, column: str) -> bool:
    row = sync_conn.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = 'report_citations'
              AND column_name = :column
            """
        ),
        {"schema": _MIGRATION_SCHEMA, "column": column},
    ).first()
    return row is not None


def _column_nullable(sync_conn: Any, column: str) -> bool:
    row = sync_conn.execute(
        text(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = 'report_citations'
              AND column_name = :column
            """
        ),
        {"schema": _MIGRATION_SCHEMA, "column": column},
    ).first()
    return bool(row is not None and row[0] == "YES")


def _named_object_exists(sync_conn: Any, name: str) -> bool:
    row = sync_conn.execute(
        text(
            """
            SELECT 1
            FROM pg_constraint WHERE conname = :name
              AND connamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)
            UNION ALL
            SELECT 1
            FROM pg_indexes WHERE indexname = :name AND schemaname = :schema
            """
        ),
        {"name": name, "schema": _MIGRATION_SCHEMA},
    ).first()
    return row is not None


@pytest.mark.asyncio
async def test_alembic_0005_report_citations_cycle(migration_dsn: str) -> None:
    """head(0005) 列/约束/索引齐全 → downgrade -1 回 0004 消失 → upgrade 恢复。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "app" / "db" / "migrations"))
    cfg.set_main_option("path_separator", "os")

    # 钉死 0005：本用例验证 0004/0005 自身循环，不随后续 head（0006+）漂移
    command.upgrade(cfg, "0005")
    engine = create_engine(migration_dsn)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0005"
            assert _column_exists(conn, "block_id")
            assert not _column_nullable(conn, "block_id")
            assert _column_nullable(conn, "claim_id")
            assert _named_object_exists(conn, "uq_citation_block_evidence")
            assert _named_object_exists(conn, "ix_report_citations_position")
            assert _named_object_exists(conn, "ix_report_citations_report_id")
            assert _named_object_exists(conn, "ix_citation_claim")

        command.downgrade(cfg, "-1")
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0004"
            assert not _column_exists(conn, "block_id")
            assert not _column_nullable(conn, "claim_id")
            assert not _named_object_exists(conn, "uq_citation_block_evidence")
            assert not _named_object_exists(conn, "ix_report_citations_position")

        command.upgrade(cfg, "0005")
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert version is not None and version[0] == "0005"
            assert _named_object_exists(conn, "uq_citation_block_evidence")
    finally:
        engine.dispose()
