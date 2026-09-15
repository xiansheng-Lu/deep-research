"""M2-4 看板六个 GET 端点的离线路由测试（AC-2 ~ AC-7）。

用按表路由的内存假 AsyncSession（仿 test_runs_api.py 思路），覆盖：
- 运行列表：创建者过滤、status 单值、project 团队校验 404、扁平分页信封；
- stages/sub-questions：归属 404、六阶段固定顺序；
- 证据池：默认剔 excluded、include_excluded、sub_question_id 跨 run 404、分页；
- 证据详情：200/404；
- 成本快照：<0.7 null、>=0.7 warning、>0.9 danger、ratio 三位小数。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1.runs import router as runs_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.base import new_ulid
from app.db.models.evidence import Evidence
from app.db.models.identity import User
from app.db.models.run import ResearchRun, Stage, SubQuestion

USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"
TEAM_ID = "01HZX7YK9P3M2V4N5J8XQRCWT7"
PROJECT_ID = "01JATESTPROJECTID000000001"
RUN_ID = "01JATESTRUNID00000000000001"

_MISSING = object()


def _now(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds)


def _user() -> User:
    return User(
        id=USER_ID,
        team_id=TEAM_ID,
        email="alice@example.com",
        hashed_password="$2b$dummy",
        display_name="Alice",
        role="researcher",
    )


def _run(
    run_id: str = RUN_ID,
    *,
    status: str = "running",
    used: int = 0,
    budget: int = 1000,
    created_offset: int = 0,
    project_id: str = PROJECT_ID,
    creator_id: str = USER_ID,
) -> ResearchRun:
    now = _now(created_offset)
    return ResearchRun(
        id=run_id,
        project_id=project_id,
        creator_id=creator_id,
        template_id="generic",
        tier="standard",
        question=f"问题-{run_id}",
        status=status,
        token_used=used,
        token_budget=budget,
        created_at=now,
        updated_at=now,
    )


def _stage(name: str, *, status: str = "succeeded", run_id: str = RUN_ID) -> Stage:
    return Stage(
        id=new_ulid(),
        run_id=run_id,
        name=name,
        status=status,
        attempt=1,
        started_at=_now(),
        finished_at=_now(1),
        created_at=_now(),
        updated_at=_now(1),
    )


def _subq(
    sq_id: str,
    *,
    run_id: str = RUN_ID,
    status: str = "succeeded",
    count: int = 1,
) -> SubQuestion:
    return SubQuestion(
        id=sq_id,
        run_id=run_id,
        question=f"子问题-{sq_id}",
        depends_on=[],
        status=status,
        evidence_count=count,
        created_at=_now(),
        updated_at=_now(),
    )


def _evidence(
    ev_id: str,
    *,
    run_id: str = RUN_ID,
    sub_question_id: str = "sq-a",
    excluded: bool = False,
    content: str | None = None,
    fetched_offset: int = 0,
) -> Evidence:
    return Evidence(
        id=ev_id,
        run_id=run_id,
        sub_question_id=sub_question_id,
        url=f"https://example.gov/{ev_id}",
        domain="example.gov",
        title=f"证据-{ev_id}",
        snippet="摘要",
        content=content,
        source_type="official_doc",
        source_level="primary",
        credibility="A",
        relevance_score=0.9,
        fingerprint=f"fp-{ev_id}",
        published_at=_now(-10),
        fetched_at=_now(fetched_offset),
        excluded_by_user=excluded,
        created_at=_now(),
        updated_at=_now(),
    )


def _predicates(stmt: Any) -> dict[str, Any]:
    """从 whereclause 抽取 {列名: 字面量}，供内存行过滤。"""
    where = stmt.whereclause
    if where is None:
        return {}
    clauses = list(where.clauses) if hasattr(where, "clauses") else [where]
    out: dict[str, Any] = {}
    for clause in clauses:
        left = getattr(getattr(clause, "left", None), "name", None)
        if left is None:
            continue
        value = getattr(clause.right, "value", _MISSING)
        if value is _MISSING:
            rendered = str(clause.right).lower()
            if "false" in rendered:
                value = False
            elif "true" in rendered:
                value = True
            else:
                continue
        out[left] = value
    return out


class _ScalarsResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _FakeSession:
    """按 SQL 文本路由到内存表的假会话；等值/IS 谓词在内存内求值。"""

    def __init__(self, rows: dict[str, list[Any]]) -> None:
        self._rows = rows

    def _table_of(self, sql: str) -> str:
        for table in ("users", "projects", "research_runs", "stages", "sub_questions", "evidence"):
            if f"FROM {table}" in sql:
                return table
        raise AssertionError(f"未预期的查询: {sql}")

    def _match(self, table: str, stmt: Any) -> list[Any]:
        preds = _predicates(stmt)
        items = [
            row
            for row in self._rows.get(table, [])
            if all(getattr(row, key, None) == value for key, value in preds.items())
        ]
        # 内存内应用 ORDER BY（多键逆序稳定排序），再做 offset/limit
        for order in reversed(list(getattr(stmt, "_order_by_clauses", ()) or ())):
            column = getattr(order, "element", order)
            name = getattr(column, "name", None)
            if name is None:
                continue
            items.sort(
                key=lambda r, n=name: getattr(r, n),
                reverse=" DESC" in str(order).upper(),
            )
        limit = getattr(stmt, "_limit", None)
        offset = getattr(stmt, "_offset", None)
        if offset is not None:
            items = items[int(offset) :]
        if limit is not None:
            items = items[: int(limit)]
        return items

    async def scalar(self, stmt: Any) -> Any:
        sql = str(stmt)
        table = self._table_of(sql)
        matched = self._match(table, stmt)
        if "count(" in sql.lower():
            return len(matched)
        return matched[0] if matched else None

    async def scalars(self, stmt: Any) -> _ScalarsResult:
        table = self._table_of(str(stmt))
        return _ScalarsResult(self._match(table, stmt))


def _client(rows: dict[str, list[Any]]) -> TestClient:
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(runs_router)
    session = _FakeSession(rows)

    async def _fake_db_session():
        yield session

    app.dependency_overrides[api_deps.db_session] = _fake_db_session

    @app.exception_handler(AppError)
    async def _app_error_handler(_request, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def auth() -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(USER_ID, settings=settings)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 运行列表
# ---------------------------------------------------------------------------


def test_list_runs_flat_envelope_and_creator_scope(auth: dict[str, str]) -> None:
    """AC-2：200 + 扁平信封；非本人 run 不可见。"""
    client = _client(
        {
            "users": [_user()],
            "research_runs": [
                _run("run-a", status="running", created_offset=-3),
                _run("run-b", status="succeeded", created_offset=-1),
                _run("run-other", creator_id="someone-else"),
            ],
        }
    )
    resp = client.get("/runs", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size", "has_more"}
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == ["run-b", "run-a"]
    assert body["has_more"] is False


def test_list_runs_status_filter_and_pagination(auth: dict[str, str]) -> None:
    """AC-2：status 单值过滤；page_size=1 时 has_more 翻转。"""
    client = _client(
        {
            "users": [_user()],
            "research_runs": [
                _run("run-1", status="running"),
                _run("run-2", status="succeeded"),
                _run("run-3", status="failed"),
            ],
        }
    )
    resp = client.get("/runs?status=running", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "running"

    page1 = client.get("/runs?page=1&page_size=1", headers=auth).json()
    page2 = client.get("/runs?page=2&page_size=1", headers=auth).json()
    assert page1["has_more"] is True
    assert page2["items"] and page2["has_more"] is True
    page4 = client.get("/runs?page=4&page_size=1", headers=auth).json()
    assert page4["items"] == []
    assert page4["has_more"] is False


def test_list_runs_page_size_over_cap_422(auth: dict[str, str]) -> None:
    """page_size>100 由校验层直接 422。"""
    client = _client({"users": [_user()], "research_runs": []})
    resp = client.get("/runs?page_size=101", headers=auth)
    assert resp.status_code == 422


def test_list_runs_project_not_in_team_404(auth: dict[str, str]) -> None:
    """project_id 非本团队项目：404，不泄漏。"""
    client = _client({"users": [_user()], "projects": [], "research_runs": []})
    resp = client.get(f"/runs?project_id={PROJECT_ID}", headers=auth)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 阶段 / 子问题
# ---------------------------------------------------------------------------


def test_list_stages_ordered_and_404(auth: dict[str, str]) -> None:
    """AC-3：乱序落库按六阶段顺序返回；他人/不存在 run 404。"""
    client = _client(
        {
            "users": [_user()],
            "research_runs": [_run()],
            "stages": [
                _stage("report", status="pending"),
                _stage("clarify"),
                _stage("retrieve", status="running"),
            ],
        }
    )
    resp = client.get(f"/runs/{RUN_ID}/stages", headers=auth)
    assert resp.status_code == 200
    assert [item["name"] for item in resp.json()] == ["clarify", "retrieve", "report"]
    assert "token_used" not in resp.json()[0]
    assert "error_code" not in resp.json()[0]

    missing = client.get("/runs/nope/stages", headers=auth)
    assert missing.status_code == 404


def test_list_sub_questions_200(auth: dict[str, str]) -> None:
    """AC-4：子问题列表字段。"""
    client = _client(
        {
            "users": [_user()],
            "research_runs": [_run()],
            "sub_questions": [_subq("sq-a"), _subq("sq-b", status="evidence_short", count=0)],
        }
    )
    resp = client.get(f"/runs/{RUN_ID}/sub-questions", headers=auth)
    assert resp.status_code == 200
    items = resp.json()
    assert [item["id"] for item in items] == ["sq-a", "sq-b"]
    assert items[0]["depends_on"] == []
    assert items[1]["evidence_count"] == 0


# ---------------------------------------------------------------------------
# 证据
# ---------------------------------------------------------------------------


def test_list_evidence_excludes_and_filters(auth: dict[str, str]) -> None:
    """AC-5：默认剔 excluded；include_excluded 带出；sub_question_id 生效。"""
    client = _client(
        {
            "users": [_user()],
            "research_runs": [_run()],
            "sub_questions": [_subq("sq-a"), _subq("sq-b")],
            "evidence": [
                _evidence("ev-1", sub_question_id="sq-a", fetched_offset=-2),
                _evidence("ev-2", sub_question_id="sq-a", fetched_offset=-1, excluded=True),
                _evidence("ev-3", sub_question_id="sq-b", fetched_offset=0),
            ],
        }
    )
    resp = client.get(f"/runs/{RUN_ID}/evidence", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size", "has_more"}
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == ["ev-3", "ev-1"]
    assert "fingerprint" not in body["items"][0]
    assert "metadata" not in body["items"][0]
    assert body["items"][0]["content"] is None

    included = client.get(f"/runs/{RUN_ID}/evidence?include_excluded=true", headers=auth)
    assert included.json()["total"] == 3

    by_sq = client.get(f"/runs/{RUN_ID}/evidence?sub_question_id=sq-a", headers=auth)
    assert by_sq.json()["total"] == 1
    assert by_sq.json()["items"][0]["id"] == "ev-1"

    foreign = client.get(f"/runs/{RUN_ID}/evidence?sub_question_id=sq-foreign", headers=auth)
    assert foreign.status_code == 404


def test_get_evidence_detail_200_and_404(auth: dict[str, str]) -> None:
    """AC-6：详情带全文；跨 run/不存在 404。"""
    client = _client(
        {
            "users": [_user()],
            "research_runs": [_run()],
            "evidence": [_evidence("ev-1", content="正文全文内容")],
        }
    )
    resp = client.get(f"/runs/{RUN_ID}/evidence/ev-1", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["content"] == "正文全文内容"

    assert client.get(f"/runs/{RUN_ID}/evidence/missing", headers=auth).status_code == 404
    assert client.get("/runs/nope/evidence/ev-1", headers=auth).status_code == 404


# ---------------------------------------------------------------------------
# 成本快照
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("used", "expected_level", "expected_ratio"),
    [
        (0, None, 0.0),
        (699, None, 0.699),
        (700, "warning", 0.7),
        (900, "warning", 0.9),
        (901, "danger", 0.901),
    ],
)
def test_cost_snapshot_levels(
    auth: dict[str, str], used: int, expected_level: str | None, expected_ratio: float
) -> None:
    """AC-7：阈值 <0.7 null / >=0.7 warning / >0.9 danger；ratio 保留三位。"""
    client = _client({"users": [_user()], "research_runs": [_run(used=used, budget=1000)]})
    resp = client.get(f"/runs/{RUN_ID}/cost/snapshot", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "used": used,
        "budget": 1000,
        "ratio": expected_ratio,
        "level": expected_level,
    }


def test_cost_snapshot_run_not_found_404(auth: dict[str, str]) -> None:
    client = _client({"users": [_user()], "research_runs": []})
    resp = client.get("/runs/missing/cost/snapshot", headers=auth)
    assert resp.status_code == 404
