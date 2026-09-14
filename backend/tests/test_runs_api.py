"""``/runs`` API 集成测试。

策略：通过 ``dependency_overrides`` 把 ``db_session`` 替换为内存假 Session，
mock ``run_research_async`` 避免真跑 LangGraph。覆盖：

- ``POST /runs`` 项目不存在 → 404
- ``POST /runs`` 合法 → 201 + 运行视图
- ``GET /runs/{id}`` → 200
- ``GET /runs/{id}`` 不存在 → 404
- ``GET /runs/{id}/report`` 报告未生成 → 422
- ``GET /runs/{id}/report`` 报告已生成 → 200
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.reports import router as reports_router
from app.api.v1.runs import router as runs_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.base import new_ulid
from app.db.models.evidence import Evidence
from app.db.models.identity import User
from app.db.models.project import Project
from app.db.models.report import Report, ReportCitation
from app.db.models.run import ResearchRun

_FAKE_USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"
_FAKE_TEAM_ID = "01HZX7YK9P3M2V4N5J8XQRCWT7"
_FAKE_PROJECT_ID = "01JATESTPROJECTID000000001"
_FAKE_RUN_ID = "01JATESTRUNID00000000000001"


def _make_fake_user() -> User:
    return User(
        id=_FAKE_USER_ID,
        team_id=_FAKE_TEAM_ID,
        email="alice@example.com",
        hashed_password="$2b$12$dummy",
        display_name="Alice",
        role="researcher",
    )


def _make_fake_project() -> Project:
    now = datetime.now(tz=UTC)
    return Project(
        id=_FAKE_PROJECT_ID,
        team_id=_FAKE_TEAM_ID,
        owner_id=_FAKE_USER_ID,
        name="测试项目",
        default_tier="standard",
        status="active",
        created_at=now,
        updated_at=now,
    )


def _make_fake_run() -> ResearchRun:
    now = datetime.now(tz=UTC)
    return ResearchRun(
        id=_FAKE_RUN_ID,
        project_id=_FAKE_PROJECT_ID,
        creator_id=_FAKE_USER_ID,
        template_id="generic",
        tier="standard",
        question="测试研究问题内容",
        status="pending",
        token_used=0,
        token_budget=150_000,
        started_at=None,
        finished_at=None,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def _make_fake_report() -> Report:
    now = datetime.now(tz=UTC)
    return Report(
        id=new_ulid(),
        run_id=_FAKE_RUN_ID,
        template_id="generic",
        status="draft",
        content_md="## 研究背景\n\n测试报告内容。",
        content_json={},
        token_used=0,
        created_at=now,
        updated_at=now,
    )


class _ScalarsResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _FakeAsyncSession:
    """可配置的 AsyncSession 替身。"""

    def __init__(self) -> None:
        self._added: list[Any] = []
        self.scalar_fn: Callable[[Any], Any] = lambda _: None
        self.scalars_fn: Callable[[Any], list[Any]] = lambda _: []

    def add(self, obj: Any) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        for obj in self._added:
            if getattr(obj, "id", None) is None:
                obj.id = new_ulid()

    async def commit(self) -> None:
        pass

    async def scalar(self, stmt: Any) -> Any:
        return self.scalar_fn(stmt)

    async def scalars(self, stmt: Any) -> _ScalarsResult:
        return _ScalarsResult(self.scalars_fn(stmt))


def _build_test_app(settings: Settings, session: _FakeAsyncSession) -> FastAPI:
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(runs_router)
    app.include_router(reports_router)

    async def _fake_db_session():
        yield session

    app.dependency_overrides[api_deps.db_session] = _fake_db_session
    app.state.session_factory = None
    app.state.hub = None
    app.state.llm = None
    app.state.retrieval_client = None

    @app.exception_handler(AppError)
    async def _app_error_handler(_request, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return app


# ====== Fixtures ======


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def session() -> _FakeAsyncSession:
    return _FakeAsyncSession()


@pytest.fixture
def client(settings: Settings, session: _FakeAsyncSession) -> TestClient:
    """已认证用户的 TestClient。"""
    session.scalar_fn = lambda _: _make_fake_user()
    app = _build_test_app(settings, session)
    return TestClient(app, raise_server_exceptions=True)


def _auth_headers(settings: Settings) -> dict[str, str]:
    token = create_access_token(_FAKE_USER_ID, settings=settings)
    return {"Authorization": f"Bearer {token}"}


# ====== 测试用例 ======


def test_create_run_project_not_found_returns_404(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """项目不存在或不属于当前团队 → 404。"""
    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        return None

    session.scalar_fn = _scalar_fn

    resp = client.post(
        "/runs",
        json={"project_id": _FAKE_PROJECT_ID, "question": "测试问题内容"},
        headers=_auth_headers(settings),
    )
    assert resp.status_code == 404


@patch("app.api.v1.runs.asyncio.create_task")
def test_create_run_returns_201(
    mock_create_task: Any,
    settings: Settings,
    client: TestClient,
    session: _FakeAsyncSession,
) -> None:
    """合法请求创建研究运行 → 201。"""
    mock_create_task.side_effect = lambda coro: coro.close()
    project = _make_fake_project()

    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        return project

    session.scalar_fn = _scalar_fn

    resp = client.post(
        "/runs",
        json={"project_id": _FAKE_PROJECT_ID, "question": "2026年中国光伏装机量趋势如何？"},
        headers=_auth_headers(settings),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] == _FAKE_PROJECT_ID
    assert body["tier"] == "standard"
    assert body["status"] == "pending"
    assert body["token_budget"] == settings.quota_tier_standard_tokens
    assert body["id"]
    assert body["stream_url"] == f"/api/v1/ws/runs/{body['id']}/stream"
    mock_create_task.assert_called_once()


def test_get_run_returns_200(settings: Settings, client: TestClient, session: _FakeAsyncSession) -> None:
    """查询存在的运行 → 200。"""
    run = _make_fake_run()

    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        return run

    session.scalar_fn = _scalar_fn

    resp = client.get(f"/runs/{_FAKE_RUN_ID}", headers=_auth_headers(settings))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == _FAKE_RUN_ID
    assert body["question"] == "测试研究问题内容"
    assert body["status"] == "pending"


def test_get_run_not_found_returns_404(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """查询不存在的运行 → 404。"""
    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        return None

    session.scalar_fn = _scalar_fn

    resp = client.get("/runs/nonexistent", headers=_auth_headers(settings))
    assert resp.status_code == 404


def test_get_run_report_not_generated_returns_422(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """报告尚未生成 → 422。"""
    run = _make_fake_run()
    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        if call_count[0] == 2:
            return run
        return None

    session.scalar_fn = _scalar_fn

    resp = client.get(f"/runs/{_FAKE_RUN_ID}/report", headers=_auth_headers(settings))
    assert resp.status_code == 422


def test_get_run_report_returns_200(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """报告已生成 → 200 + Markdown。"""
    run = _make_fake_run()
    report = _make_fake_report()

    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        if call_count[0] == 2:
            return run
        return report

    session.scalar_fn = _scalar_fn

    resp = client.get(f"/runs/{_FAKE_RUN_ID}/report", headers=_auth_headers(settings))
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == _FAKE_RUN_ID
    assert "## 研究背景" in body["content_md"]
    assert body["status"] == "draft"


def test_get_report_by_run_returns_200(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """``GET /reports/{run_id}`` → 200 + Markdown。"""
    run = _make_fake_run()
    report = _make_fake_report()

    call_count = [0]

    def _scalar_fn(_stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_fake_user()
        if call_count[0] == 2:
            return run
        return report

    session.scalar_fn = _scalar_fn

    resp = client.get(f"/reports/{_FAKE_RUN_ID}", headers=_auth_headers(settings))
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == _FAKE_RUN_ID
    assert "## 研究背景" in body["content_md"]


# ====== M2-7：结构化终稿超集与信源索引 ======


def _make_final_report() -> Report:
    now = datetime.now(tz=UTC)
    return Report(
        id=new_ulid(),
        run_id=_FAKE_RUN_ID,
        template_id="generic",
        status="final",
        content_md="## 研究背景\n\n终稿。",
        content_json={
            "outline": [
                {"id": "sec-overview", "title": "调研概述", "type": "conclusion"},
                {"id": "sec-findings", "title": "核心发现", "type": "conclusion"},
                {"id": "sec-limitations", "title": "局限与建议", "type": "limitation"},
            ],
            "blocks": [
                {
                    "id": "block-01",
                    "type": "conclusion",
                    "claim_id": "claim-01",
                    "text": "两条技术路线已清晰",
                    "confidence": "cross_verified",
                    "citations": [{"evidence_id": "ev1", "marker": "[1]", "snippet": "证据一原文片段"}],
                },
                {
                    "id": "block-02",
                    "type": "limitation",
                    "text": "样本范围有限",
                    "citations": [],
                },
            ],
            "citation_audit": {
                "claim_blocks": 1,
                "bound_blocks": 1,
                "forced_inferred_blocks": 0,
                "numeric_claim_binding_rate": 1.0,
            },
        },
        token_used=120,
        created_at=now,
        updated_at=now,
    )


def _make_evidence(ev_id: str, *, url: str, title: str) -> Evidence:
    now = datetime.now(tz=UTC)
    return Evidence(
        id=ev_id,
        run_id=_FAKE_RUN_ID,
        sub_question_id="sq1",
        url=url,
        domain="example.com",
        title=title,
        snippet=f"{title}片段",
        source_type="official_doc",
        source_level="primary",
        credibility="A",
        fingerprint=f"fp-{ev_id}",
        published_at=None,
        fetched_at=now,
    )


def _make_citation(
    cid: str, *, block_id: str, evidence_id: str, position: int, snippet: str
) -> ReportCitation:
    return ReportCitation(
        id=cid,
        report_id="rpt1",
        block_id=block_id,
        evidence_id=evidence_id,
        claim_id="claim-01" if block_id != "block-02" else None,
        position=position,
        snippet=snippet,
    )


def _bind_run_and_report(session: _FakeAsyncSession, report: Report) -> None:
    run = _make_fake_run()
    answers = {"User": _make_fake_user(), "ResearchRun": run, "Report": report}

    def _scalar_fn(stmt: Any) -> Any:
        # 按语句实体分发（select(Entity.id) 的 name 是列名，取 entity 类名）
        description = stmt.column_descriptions[0]
        entity = description.get("entity")
        name = entity.__name__ if entity is not None else description["name"]
        return answers.get(name)

    session.scalar_fn = _scalar_fn


def test_final_report_response_contains_outline_and_blocks(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """final 报告两查询端点都带 outline/blocks 超集。"""
    report = _make_final_report()
    _bind_run_and_report(session, report)

    for path in (f"/reports/{_FAKE_RUN_ID}", f"/runs/{_FAKE_RUN_ID}/report"):
        body = client.get(path, headers=_auth_headers(settings)).json()
        assert body["status"] == "final"
        assert [s["id"] for s in body["outline"]] == [
            "sec-overview",
            "sec-findings",
            "sec-limitations",
        ]
        assert body["blocks"][0]["citations"][0]["marker"] == "[1]"
        assert body["blocks"][1]["claim_id"] is None


def test_get_citations_returns_dedup_sorted_index(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """信源索引按 marker 升序去重，元素含 evidence_id + 九展示字段。"""
    report = _make_final_report()
    _bind_run_and_report(session, report)
    citation_rows = [
        _make_citation("rc1", block_id="block-01", evidence_id="ev1", position=1, snippet="片段一"),
        _make_citation("rc2", block_id="block-03", evidence_id="ev2", position=2, snippet="片段二"),
        _make_citation("rc3", block_id="block-04", evidence_id="ev1", position=1, snippet="片段一"),
    ]
    evidence_rows = [
        _make_evidence("ev1", url="https://example.com/a", title="来源甲"),
        _make_evidence("ev2", url="https://example.com/b", title="来源乙"),
    ]
    scalars_count = [0]

    def _scalars_fn(_stmt: Any) -> list[Any]:
        scalars_count[0] += 1
        return citation_rows if scalars_count[0] == 1 else evidence_rows

    session.scalars_fn = _scalars_fn

    resp = client.get(f"/reports/{_FAKE_RUN_ID}/citations", headers=_auth_headers(settings))
    assert resp.status_code == 200
    items = resp.json()
    assert [item["marker"] for item in items] == ["[1]", "[2]"]
    first = items[0]
    assert set(first) == {
        "evidence_id",
        "marker",
        "snippet",
        "url",
        "title",
        "domain",
        "source_type",
        "source_level",
        "credibility",
        "published_at",
    }
    assert first["evidence_id"] == "ev1"
    assert first["url"] == "https://example.com/a"
    assert first["credibility"] == "A"


def test_get_citations_draft_report_returns_empty_list(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """draft 报告（无引文行）→ 200 空列表。"""
    _bind_run_and_report(session, _make_fake_report())

    resp = client.get(f"/reports/{_FAKE_RUN_ID}/citations", headers=_auth_headers(settings))
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_citations_report_not_generated_returns_422(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """报告未生成 → 422 validation_error。"""
    run = _make_fake_run()
    answers = iter([_make_fake_user(), run, None])
    session.scalar_fn = lambda _: next(answers)

    resp = client.get(f"/reports/{_FAKE_RUN_ID}/citations", headers=_auth_headers(settings))
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_get_citations_run_not_found_returns_404(
    settings: Settings, client: TestClient, session: _FakeAsyncSession
) -> None:
    """run 不存在/非属主 → 404。"""
    answers = iter([_make_fake_user(), None])
    session.scalar_fn = lambda _: next(answers)

    resp = client.get(f"/reports/{_FAKE_RUN_ID}/citations", headers=_auth_headers(settings))
    assert resp.status_code == 404
