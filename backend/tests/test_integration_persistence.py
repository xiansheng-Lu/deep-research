"""M2-2 Task 3 过程数据落库的真实 Postgres 集成测试。

CI 无外部数据库时自动跳过；本地以 WSL2 Docker 内 dr_postgres 为目标：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research

隔离方式：每个测试模块会话使用独立 schema ``m22_task3``（search_path 追加 public
以解析 pgvector 扩展类型），setup 重建、teardown DROP CASCADE，不污染业务库。

覆盖 TR-3.1 / TR-3.2 / TR-3.3：两表行数与 ID 集合和 state 一致、恢复重放幂等、
evidence 列级默认值（excluded_by_user / metadata_ / fetched_at）真实生效。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import create_all_in_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 导入以注册全部表到 Base.metadata
    Evidence,
    Project,
    ResearchRun,
    SubQuestion,
    Team,
    User,
)
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes import standardizer
from app.orchestrator.persistence import persist_evidence, persist_sub_questions

pytestmark = pytest.mark.integration

# 独立测试 schema：与业务表隔离，public 仅用于解析 pgvector 的 vector 类型
_SCHEMA = "m22_task3"
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
    except Exception as exc:  # noqa: BLE001 - CI 无 PG 时整体跳过而非失败
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过过程数据落库集成测试：{exc!r}")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed_run(session: AsyncSession) -> str:
    """插入最小外键链 Team→User→Project→ResearchRun，返回 run_id。"""
    team = Team(id=new_ulid(), name="集成测试团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=f"{new_ulid().lower()}@example.com",
        hashed_password="x",
        display_name="集成测试用户",
        role="owner",
    )
    project = Project(
        id=new_ulid(),
        team_id=team.id,
        owner_id=user.id,
        name="集成测试项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="quick",
        question="过程数据落库集成测试问题",
        token_budget=100_000,
    )
    session.add_all([team, user, project, run])
    await session.flush()
    return run.id


def _sq(sq_id: str, **kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": sq_id,
        "question": f"子问题-{sq_id[-6:]}",
        "depends_on": [],
        "status": "pending",
        "evidence_ids": [],
    }
    payload.update(kwargs)
    return payload


def _ev(ev_id: str, sq_id: str, *, fingerprint: str | None = None) -> dict[str, Any]:
    return {
        "id": ev_id,
        "sub_question_id": sq_id,
        "url": f"https://example.com/{ev_id}",
        "domain": "example.com",
        "title": f"证据标题-{ev_id[-6:]}",
        "snippet": "证据摘要正文内容",
        "source_type": "news",
        "source_level": "secondary",
        "credibility": "B",
        "relevance_score": 0.8,
        "fingerprint": fingerprint or f"fp-{ev_id}",
        "published_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
        "fetched_at": datetime(2026, 9, 2, tzinfo=UTC).isoformat(),
    }


async def _count(session: AsyncSession, model: type[Any], run_id: str) -> int:
    return int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM {model.__tablename__} WHERE run_id = :rid").bindparams(rid=run_id)
            )
        ).scalar_one()
    )


async def test_rows_match_state_ids_and_defaults(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TR-3.1/3.3：两表行数与 ID 集合和 state 完全一致，默认值正确（AC-11）。"""
    async with session_factory() as session:
        run_id = await _seed_run(session)
        sq1 = _sq(new_ulid(), status="succeeded")
        sq2 = _sq(new_ulid(), depends_on=[sq1["id"]], status="succeeded")
        evs = [
            _ev(new_ulid(), sq1["id"]),
            _ev(new_ulid(), sq1["id"]),
            _ev(new_ulid(), sq2["id"]),
        ]
        sq1["evidence_ids"] = [evs[0]["id"], evs[1]["id"]]
        sq2["evidence_ids"] = [evs[2]["id"]]

        await persist_sub_questions(session, run_id=run_id, items=[sq1, sq2])
        await persist_evidence(session, run_id=run_id, items=evs)
        await session.flush()

        # 行数与 state 一致
        assert await _count(session, SubQuestion, run_id) == 2
        assert await _count(session, Evidence, run_id) == 3

        # ID 集合一致（ULID 主键即 state 中的 ID，冲突外键可连）
        sq_rows = (
            await session.execute(
                text(
                    "SELECT id, status, evidence_count FROM sub_questions "
                    "WHERE run_id = :rid ORDER BY question"
                ).bindparams(rid=run_id)
            )
        ).all()
        assert {row.id for row in sq_rows} == {sq1["id"], sq2["id"]}
        counts = {row.id: row.evidence_count for row in sq_rows}
        assert counts == {sq1["id"]: 2, sq2["id"]: 1}
        assert {row.status for row in sq_rows} == {"succeeded"}

        ev_rows = (
            await session.execute(
                text(
                    "SELECT id, sub_question_id, excluded_by_user, metadata, "
                    "fetched_at, source_level, credibility FROM evidence "
                    "WHERE run_id = :rid"
                ).bindparams(rid=run_id)
            )
        ).all()
        assert {row.id for row in ev_rows} == {e["id"] for e in evs}
        # 外键有效：每条证据关联的子问题都属于本 run
        assert {row.sub_question_id for row in ev_rows} == {sq1["id"], sq2["id"]}
        # 列级默认值真实生效（TR-3.3）
        assert all(row.excluded_by_user is False for row in ev_rows)
        assert all(row.metadata == {} for row in ev_rows)
        assert all(row.fetched_at is not None for row in ev_rows)
        assert {row.source_level for row in ev_rows} == {"secondary"}
        assert {row.credibility for row in ev_rows} == {"B"}
        await session.rollback()


async def test_replay_does_not_duplicate_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TR-3.2：恢复重放（重复调用 + 状态推进 upsert）不产生重复行。"""
    async with session_factory() as session:
        run_id = await _seed_run(session)
        sq = _sq(new_ulid(), status="running", evidence_ids=[])
        ev = _ev(new_ulid(), sq["id"])

        await persist_sub_questions(session, run_id=run_id, items=[sq])
        await persist_evidence(session, run_id=run_id, items=[ev])
        await session.flush()

        # 模拟回流后的第二跳：子问题成功并回填证据引用，证据重复触达
        sq_done = _sq(sq["id"], status="succeeded", evidence_ids=[ev["id"]])
        await persist_sub_questions(session, run_id=run_id, items=[sq_done])
        await persist_evidence(session, run_id=run_id, items=[ev])
        await session.flush()

        assert await _count(session, SubQuestion, run_id) == 1
        assert await _count(session, Evidence, run_id) == 1

        status_count = (
            await session.execute(
                text("SELECT status, evidence_count FROM sub_questions WHERE id = :sid").bindparams(
                    sid=sq["id"]
                )
            )
        ).one()
        assert status_count.status == "succeeded"
        assert status_count.evidence_count == 1
        await session.rollback()


async def test_standardizer_node_persists_classified_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """节点级链路：standardize 分类后证据与收敛后的子问题引用真实落库。"""
    async with session_factory() as session:
        run_id = await _seed_run(session)
        sq = _sq(new_ulid(), status="succeeded")
        kept = _ev(new_ulid(), sq["id"], fingerprint="fp-keep")
        # 同指纹重复证据：standardize 跨证据去重后仅保留 1 条
        duplicate = _ev(new_ulid(), sq["id"], fingerprint="fp-keep")
        sq["evidence_ids"] = [kept["id"], duplicate["id"]]
        await persist_sub_questions(session, run_id=run_id, items=[sq])
        await session.flush()

        deps = NodeDeps(run_id=run_id, team_id="t1", trace_id="tr1", db_session=session)
        state: dict[str, Any] = {
            "run_id": run_id,
            "sub_questions": [sq],
            "evidence": [kept, duplicate],
        }
        patch = await standardizer.run(state, deps=deps)

        classified = patch["standardized_evidence"]
        assert [e["id"] for e in classified] == [kept["id"]]
        await session.flush()

        assert await _count(session, Evidence, run_id) == 1
        # 子问题引用已收敛为去重后的单证据
        sq_count = (
            await session.execute(
                text("SELECT evidence_count FROM sub_questions WHERE id = :sid").bindparams(sid=sq["id"])
            )
        ).scalar_one()
        assert sq_count == 1
        await session.rollback()
