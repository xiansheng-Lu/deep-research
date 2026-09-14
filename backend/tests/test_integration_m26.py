"""M2-6 信源元数据抽取真库集成测试（AC-10）。

CI 无外部数据库时自动跳过；独立 schema ``m26_orm``（ORM 建表，search_path
追加 public 解析 pgvector），setup 重建、teardown DROP CASCADE。

覆盖：retrieve 态证据经 standardizer 真实分类/分级/留痕后落库，独立会话
读回 source_type/source_level/credibility/relevance_score/metadata_ 全部
正确（JSONB 往返），且权威域名证据获得真实 A/B 档（修复政府站评 C）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from conftest import create_all_in_schema
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 注册全部表
    Evidence,
    Project,
    ResearchRun,
    SubQuestion,
    Team,
    User,
)
from app.orchestrator.nodes import standardizer
from app.orchestrator.persistence import persist_evidence
from app.orchestrator.state import EvidenceDict, SubQuestionDict

pytestmark = pytest.mark.integration

_ORM_SCHEMA = "m26_orm"
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"


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
        pytest.skip(f"测试库不可达，跳过 M2-6 集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_ORM_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_chain(session: AsyncSession) -> tuple[str, str]:
    """插入 Team→User→Project→Run→SubQuestion，返回 (run_id, sub_question_id)。"""
    team = Team(id=new_ulid(), name="M26团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="M26用户",
        role="owner",
    )
    project = Project(
        id=new_ulid(),
        team_id=team.id,
        owner_id=user.id,
        name="M26项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="standard",
        question="信源元数据落库集成测试",
        status="running",
        token_budget=100_000,
    )
    subq = SubQuestion(
        id=new_ulid(),
        run_id=run.id,
        question="新能源汽车销量",
        depends_on=[],
        status="succeeded",
        evidence_count=0,
    )
    session.add_all([team, user, project, run, subq])
    await session.commit()
    return run.id, subq.id


def _retrieve_evidence(
    *,
    subq_id: str,
    ev_id: str,
    domain: str,
    url: str,
    relevance: float,
    published_at: str | None,
) -> EvidenceDict:
    """构造 retrieve 态证据：中性占位 + fan-out 已写的相关性/日期来源留痕。"""
    return {
        "id": ev_id,
        "sub_question_id": subq_id,
        "url": url,
        "domain": domain,
        "title": "新能源汽车销量创新高",
        "snippet": "新能源汽车销量大幅增长",
        "source_type": "search",  # retrieve 中性占位
        "source_level": "tertiary",
        "credibility": "C",
        "fingerprint": f"fp-{ev_id}",
        "published_at": published_at,
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "relevance_score": relevance,
        "metadata_": {
            "relevance": {"provider": 0.0, "lexical": relevance, "blended": relevance},
            "published_at_source": "provider" if published_at else "null",
        },
    }


async def test_classified_evidence_persisted_with_real_metadata(
    orm_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC-10：权威域名证据经 standardizer 后真实落 A 档，元数据 JSONB 往返正确。"""
    async with orm_factory() as session:
        run_id, subq_id = await _seed_chain(session)

        raw = [
            # 政府统计站、高相关 → official_doc/primary/A，有发布时间
            _retrieve_evidence(
                subq_id=subq_id,
                ev_id="ev-gov",
                domain="stats.gov.cn",
                url="https://stats.gov.cn/tjgb/2026",
                relevance=0.86,
                published_at="2026-05-01T00:00:00+00:00",
            ),
            # 主流媒体、低相关 → news/secondary/C（只降一档）
            _retrieve_evidence(
                subq_id=subq_id,
                ev_id="ev-news",
                domain="reuters.com",
                url="https://reuters.com/markets/auto",
                relevance=0.1,
                published_at=None,
            ),
            # 社区、高相关 → community/tertiary/C
            _retrieve_evidence(
                subq_id=subq_id,
                ev_id="ev-community",
                domain="zhihu.com",
                url="https://zhihu.com/question/123",
                relevance=0.7,
                published_at=None,
            ),
        ]
        subqs: list[SubQuestionDict] = [
            {
                "id": subq_id,
                "question": "新能源汽车销量",
                "depends_on": [],
                "status": "succeeded",
                "evidence_ids": ["ev-gov", "ev-news", "ev-community"],
            }
        ]
        patch = await standardizer.run(
            {"run_id": run_id, "evidence": raw, "sub_questions": subqs},
            deps=None,
        )
        classified = patch["standardized_evidence"]
        await persist_evidence(session, run_id=run_id, items=classified)
        await session.commit()

    # 独立会话读回，按可信度排序：A(gov) → C(news) 与 C(community) 同级按分数
    async with orm_factory() as ro:
        rows = (await ro.scalars(select(Evidence).where(Evidence.run_id == run_id))).all()
        by_domain = {row.domain: row for row in rows}
        assert len(rows) == 3

        gov = by_domain["stats.gov.cn"]
        assert gov.source_type == "official_doc"
        assert gov.source_level == "primary"
        assert gov.credibility == "A"  # 修复「政府站评 C」
        assert float(gov.relevance_score) == 0.86
        assert gov.published_at is not None
        gov_meta = gov.metadata_
        assert gov_meta["source_rule"].startswith("PRIMARY_OFFICIAL")
        assert gov_meta["published_at_source"] == "provider"
        assert "missing_fields" not in gov_meta
        assert gov_meta["relevance"]["blended"] == 0.86

        news = by_domain["reuters.com"]
        assert news.source_type == "news"
        assert news.source_level == "secondary"
        assert news.credibility == "C"  # 低相关只降一档（B→C）
        assert news.published_at is None
        news_meta = news.metadata_
        assert news_meta["source_rule"].startswith("SECONDARY_NEWS")
        assert news_meta["missing_fields"] == ["published_at"]

        community = by_domain["zhihu.com"]
        assert community.source_type == "community"
        assert community.source_level == "tertiary"
        assert community.credibility == "C"  # 高相关维持基础档
        assert community.metadata_["source_rule"].startswith("TERTIARY_COMMUNITY")
