"""M2-2 Task 7 分歧三端点真实 Postgres API 集成测试。

CI 无外部数据库时自动跳过；本地以 WSL2 Docker 内 dr_postgres 为目标：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research

隔离方式：独立 schema ``m22_task7``（search_path 追加 public 解析 pgvector），
每个用例 setup 重建、teardown DROP CASCADE。

覆盖 TR-7.1（列表 200/排序/401/404）、TR-7.2（详情八项证据摘要/404）、
TR-7.3（合法裁决 200/落库、422 枚举与空 reason、409 重复裁决）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import create_all_in_schema
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps as api_deps
from app.api.v1.conflicts import router as conflicts_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.base import new_ulid
from app.db.models import (  # noqa: F401 - 注册全部表
    Conflict,
    Evidence,
    Project,
    ResearchRun,
    SubQuestion,
    Team,
    User,
    Verdict,
)

pytestmark = pytest.mark.integration

_SCHEMA = "m22_task7"
_DEFAULT_DSN = "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research"


@pytest.fixture
async def context() -> AsyncIterator[dict[str, Any]]:
    """构造真库引擎、最小 FastAPI 应用与 httpx 客户端；库不可达则 skip。"""
    dsn = os.environ.get("TEST_DATABASE_URL", _DEFAULT_DSN)
    engine = create_async_engine(
        dsn,
        pool_size=2,
        connect_args={"server_settings": {"search_path": f"{_SCHEMA},public"}},
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
        async with engine.begin() as conn:
            # 显式指定 schema：create_all 的 checkfirst 默认查 public 同名表会
            # 跳过建表，导致隔离失效（见 conftest.create_all_in_schema）
            await conn.run_sync(create_all_in_schema, _SCHEMA)
    except Exception as exc:  # noqa: BLE001 - CI 无 PG 时整体跳过
        await engine.dispose()
        pytest.skip(f"测试库不可达，跳过冲突 API 集成测试：{exc!r}")

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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "maker": maker, "engine": engine}

    # 先清理独立 schema 再释放连接池
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    await engine.dispose()


# ---------------------------------------------------------------------------
# 造数辅助
# ---------------------------------------------------------------------------


async def _seed_team_user(session: AsyncSession, *, email: str = "owner@example.com") -> tuple[Team, User]:
    team = Team(id=new_ulid(), name="冲突 API 测试团队", plan="free", settings={})
    user = User(
        id=new_ulid(),
        team_id=team.id,
        email=email,
        hashed_password="x",
        display_name="冲突 API 用户",
        role="owner",
    )
    session.add_all([team, user])
    await session.flush()
    return team, user


async def _seed_run(session: AsyncSession, user: User) -> ResearchRun:
    project = Project(
        id=new_ulid(),
        team_id=user.team_id,
        owner_id=user.id,
        name="冲突 API 测试项目",
        status="active",
    )
    run = ResearchRun(
        id=new_ulid(),
        project_id=project.id,
        creator_id=user.id,
        template_id="generic",
        tier="standard",
        question="2026 年光伏新增装机各方口径是否冲突",
        token_budget=100_000,
    )
    session.add_all([project, run])
    await session.flush()
    return run


async def _seed_evidence_pair(session: AsyncSession, run: ResearchRun) -> tuple[Evidence, Evidence]:
    sq = SubQuestion(
        id=new_ulid(),
        run_id=run.id,
        question="光伏新增装机口径",
        depends_on=[],
        status="succeeded",
        evidence_count=2,
    )
    session.add(sq)
    await session.flush()
    base = datetime(2026, 9, 1, tzinfo=UTC)
    rows: list[Evidence] = []
    for idx, (title, snippet, credibility) in enumerate(
        [
            ("统计局报告", "新增装机 210GW，同比增长 45%", "A"),
            ("行业协会报告", "按并网口径实际仅 120GW", "C"),
        ]
    ):
        ev = Evidence(
            id=new_ulid(),
            run_id=run.id,
            sub_question_id=sq.id,
            url=f"https://example.com/{idx}/pv",
            domain="example.com",
            title=title,
            snippet=snippet,
            source_type="news",
            source_level="secondary",
            credibility=credibility,
            relevance_score=0.9,
            fingerprint=f"fp-{run.id}-{idx}",
            published_at=base,
            fetched_at=base + timedelta(days=1),
        )
        session.add(ev)
        rows.append(ev)
    await session.flush()
    return rows[0], rows[1]


async def _seed_conflict(
    session: AsyncSession,
    run: ResearchRun,
    ev_a: Evidence,
    ev_b: Evidence,
    *,
    status: str = "awaiting_human",
    created_at: datetime | None = None,
) -> Conflict:
    now = datetime.now(tz=UTC)
    conflict = Conflict(
        id=new_ulid(),
        run_id=run.id,
        claim="2026 年光伏新增装机规模",
        evidence_a_id=ev_a.id,
        evidence_b_id=ev_b.id,
        type="methodological",
        severity="high",
        status=status,
        created_at=created_at or now,
        updated_at=created_at or now,
    )
    session.add(conflict)
    await session.flush()
    return conflict


def _auth_headers(user_id: str) -> dict[str, str]:
    token = create_access_token(user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# TR-7.1 列表
# ---------------------------------------------------------------------------


async def test_list_conflicts_200_ordered(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        ev_a, ev_b = await _seed_evidence_pair(session, run)
        older = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        newer = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
        c1 = await _seed_conflict(session, run, ev_a, ev_b, created_at=older)
        c2 = await _seed_conflict(session, run, ev_a, ev_b, created_at=newer)
        await session.commit()

    resp = await client.get(f"/api/v1/runs/{run.id}/conflicts", headers=_auth_headers(user.id))
    assert resp.status_code == 200
    items = resp.json()
    assert [item["id"] for item in items] == [c1.id, c2.id]
    first = items[0]
    assert set(first) == {
        "id",
        "run_id",
        "claim",
        "evidence_a_id",
        "evidence_b_id",
        "type",
        "severity",
        "status",
        "created_at",
        "updated_at",
    }
    assert first["run_id"] == run.id
    assert first["type"] == "methodological"
    assert first["severity"] == "high"
    assert first["status"] == "awaiting_human"


async def test_list_conflicts_requires_token(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        await session.commit()

    resp = await client.get(f"/api/v1/runs/{run.id}/conflicts")
    assert resp.status_code == 401


async def test_list_conflicts_non_owner_404(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, owner = await _seed_team_user(session, email="owner@example.com")
        run = await _seed_run(session, owner)
        _, other = await _seed_team_user(session, email="other@example.com")
        await session.commit()

    resp = await client.get(f"/api/v1/runs/{run.id}/conflicts", headers=_auth_headers(other.id))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TR-7.2 详情
# ---------------------------------------------------------------------------


async def test_conflict_detail_embeds_evidence(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        ev_a, ev_b = await _seed_evidence_pair(session, run)
        conflict = await _seed_conflict(session, run, ev_a, ev_b)
        await session.commit()

    resp = await client.get(f"/api/v1/conflicts/{conflict.id}", headers=_auth_headers(user.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == conflict.id

    expected_evidence_fields = {
        "id",
        "title",
        "url",
        "domain",
        "snippet",
        "credibility",
        "source_type",
        "published_at",
    }
    for side, ev in (("evidence_a", ev_a), ("evidence_b", ev_b)):
        summary = body[side]
        assert set(summary) == expected_evidence_fields
        assert summary["id"] == ev.id
        assert summary["title"] == ev.title
        assert summary["url"] == ev.url
        assert summary["domain"] == "example.com"
        assert summary["credibility"] == ev.credibility
        assert summary["source_type"] == "news"
        assert summary["published_at"] is not None


async def test_conflict_detail_unknown_404(context: dict[str, Any]) -> None:
    client: AsyncClient = context["client"]
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        await session.commit()

    resp = await client.get(f"/api/v1/conflicts/{new_ulid()}", headers=_auth_headers(user.id))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TR-7.3 裁决
# ---------------------------------------------------------------------------


async def test_submit_verdict_200_and_persists(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        ev_a, ev_b = await _seed_evidence_pair(session, run)
        conflict = await _seed_conflict(session, run, ev_a, ev_b)
        await session.commit()

    resp = await client.post(
        f"/api/v1/conflicts/{conflict.id}/verdict",
        headers=_auth_headers(user.id),
        json={
            "choice": "both",
            "reason": "  双方统计口径不同，观点并存  ",
            "additional_note": "  引用时注明口径 ",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "conflict_id": conflict.id,
        "status": "resolved",
        "verdict_id": body["verdict_id"],
    }
    assert len(body["verdict_id"]) == 26

    async with maker() as session:
        refreshed = await session.get(Conflict, conflict.id)
        assert refreshed is not None
        assert refreshed.status == "resolved"
        assert refreshed.resolved_at is not None
        verdict_row = (
            await session.execute(
                text(
                    "SELECT choice, reason, additional_note, user_id FROM verdicts WHERE conflict_id = :cid"
                ).bindparams(cid=conflict.id)
            )
        ).one()
        assert verdict_row.choice == "both"
        # reason/note 已 strip
        assert verdict_row.reason == "双方统计口径不同，观点并存"
        assert verdict_row.additional_note == "引用时注明口径"
        assert verdict_row.user_id == user.id


async def test_submit_verdict_invalid_choice_422(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        ev_a, ev_b = await _seed_evidence_pair(session, run)
        conflict = await _seed_conflict(session, run, ev_a, ev_b)
        await session.commit()

    resp = await client.post(
        f"/api/v1/conflicts/{conflict.id}/verdict",
        headers=_auth_headers(user.id),
        json={"choice": "evidence_c", "reason": "非法枚举"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("reason", ["", "   "])
async def test_submit_verdict_blank_reason_422(context: dict[str, Any], reason: str) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        ev_a, ev_b = await _seed_evidence_pair(session, run)
        conflict = await _seed_conflict(session, run, ev_a, ev_b)
        await session.commit()

    resp = await client.post(
        f"/api/v1/conflicts/{conflict.id}/verdict",
        headers=_auth_headers(user.id),
        json={"choice": "evidence_a", "reason": reason},
    )
    assert resp.status_code == 422


async def test_submit_verdict_duplicate_409(context: dict[str, Any]) -> None:
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    client: AsyncClient = context["client"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        run = await _seed_run(session, user)
        ev_a, ev_b = await _seed_evidence_pair(session, run)
        conflict = await _seed_conflict(session, run, ev_a, ev_b)
        await session.commit()

    first = await client.post(
        f"/api/v1/conflicts/{conflict.id}/verdict",
        headers=_auth_headers(user.id),
        json={"choice": "evidence_a", "reason": "采信 A"},
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/conflicts/{conflict.id}/verdict",
        headers=_auth_headers(user.id),
        json={"choice": "reject", "reason": "改判双弃"},
    )
    assert second.status_code == 409

    # 409 回滚后仍只有 1 条 Verdict，冲突保持首次裁决结果
    async with maker() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM verdicts WHERE conflict_id = :cid").bindparams(cid=conflict.id)
            )
        ).scalar_one()
        assert count == 1
        refreshed = await session.get(Conflict, conflict.id)
        assert refreshed is not None
        assert refreshed.status == "resolved"


async def test_submit_verdict_unknown_conflict_404(context: dict[str, Any]) -> None:
    client: AsyncClient = context["client"]
    maker: async_sessionmaker[AsyncSession] = context["maker"]
    async with maker() as session:
        _, user = await _seed_team_user(session)
        await session.commit()

    resp = await client.post(
        f"/api/v1/conflicts/{new_ulid()}/verdict",
        headers=_auth_headers(user.id),
        json={"choice": "both", "reason": "对不存在冲突裁决"},
    )
    assert resp.status_code == 404
