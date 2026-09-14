"""M2-5 pause/cancel 控制端点的离线路由测试（AC-1/AC-3 错误路径矩阵）。

用内存假会话模拟条件 UPDATE（按 id+status/IN 谓词命中并改内存行），注册表注入
假任务句柄（不调度真实协程）；协程内收尾由 test_executor.py 覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.api import deps as api_deps
from app.api.v1 import runs as runs_module
from app.api.v1.runs import router as runs_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.models.evidence import Evidence
from app.db.models.identity import User
from app.db.models.intervention import RunIntervention
from app.db.models.run import ResearchRun, SubQuestion
from app.orchestrator.registry import RunRegistry

USER_ID = "01HZX7YK9P3M2V4N5J8XQRCWT6"
TEAM_ID = "01HZX7YK9P3M2V4N5J8XQRCWT7"
RUN_ID = "01JATESTRUNID00000000000001"


def _user() -> User:
    return User(
        id=USER_ID,
        team_id=TEAM_ID,
        email="alice@example.com",
        hashed_password="$2b$dummy",
        display_name="Alice",
        role="researcher",
    )


def _run(status: str = "running") -> ResearchRun:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    return ResearchRun(
        id=RUN_ID,
        project_id="proj-1",
        creator_id=USER_ID,
        template_id="generic",
        tier="standard",
        question="测试问题内容",
        status=status,
        token_used=120,
        token_budget=1000,
        created_at=now,
        updated_at=now,
    )


class _FakeTask:
    """注册表用假协程句柄：记录 cancel 调用，不调度真实任务。"""

    def __init__(self, *, done: bool = False) -> None:
        self._done = done
        self.cancel_calls = 0

    def done(self) -> bool:
        return self._done

    def cancel(self) -> bool:
        self.cancel_calls += 1
        return True


class _UpdateResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def _column_values(clause: Any) -> tuple[str | None, list[Any]]:
    """从 where 子句抽取 (列名, 候选值列表)；等值给单值，IN 给值列表。"""
    left = getattr(getattr(clause, "left", None), "name", None)
    if left is None:
        return None, []
    right = getattr(clause, "right", None)
    # IN/等值在 SQLAlchemy 2.x 右侧统一为 BindParameter；expanding IN 的 value 是 list
    value = getattr(right, "value", None)
    if isinstance(value, list | tuple):
        return left, list(value)
    return left, [value]


class _ControlFakeSession:
    """支持 scalar 查询与条件 UPDATE 的内存会话（仅控制端点场景）。"""

    def __init__(
        self,
        users: list[User],
        runs: list[ResearchRun],
        sub_questions: list[SubQuestion] | None = None,
        evidences: list[Evidence] | None = None,
    ) -> None:
        self._users = users
        self._runs = runs
        self._sub_questions = sub_questions or []
        self._evidences = evidences or []
        self.interventions: list[RunIntervention] = []

    async def scalar(self, stmt: Any) -> Any:
        sql = str(stmt)
        if "FROM reports" in sql:
            # 控制端点只查既有报告 id；测试场景统一无报告
            return None
        if "FROM projects" in sql:
            # read_run_interrupt 读取 team_id
            return TEAM_ID
        if "FROM run_interventions" in sql:
            # 幂等键查询：按 (run_id, idempotency_key) 等值匹配
            preds = self._predicates(stmt)
            for item in self.interventions:
                if all(getattr(item, k, None) == v for k, v in preds.items()):
                    return item
            return None
        if "FROM sub_questions" in sql:
            preds = self._predicates(stmt)
            for sq in self._sub_questions:
                if all(getattr(sq, k, None) == v for k, v in preds.items()):
                    return sq
            return None
        if "FROM users" in sql:
            rows: list[Any] = self._users
        elif "FROM research_runs" in sql:
            rows = self._runs
        else:
            raise AssertionError(f"未预期的查询: {sql}")
        preds = self._predicates(stmt)
        for row in rows:
            if all(getattr(row, k, None) == v for k, v in preds.items()):
                return row
        return None

    @staticmethod
    def _predicates(stmt: Any) -> dict[str, Any]:
        preds: dict[str, Any] = {}
        where = getattr(stmt, "whereclause", None)
        if where is not None:
            for clause in getattr(where, "clauses", [where]):
                name, values = _column_values(clause)
                if name is not None and len(values) == 1:
                    preds[name] = values[0]
        return preds

    async def execute(self, stmt: Any) -> _UpdateResult:
        table = stmt.table.name
        # SET 值：Update._values 为 {Column: BindParameter}
        values: dict[str, Any] = {col.name: bind.value for col, bind in stmt._values.items()}
        allowed: dict[str, list[Any]] = {}
        where = getattr(stmt, "whereclause", None)
        for clause in getattr(where, "clauses", [where]):
            name, col_values = _column_values(clause)
            if name is not None:
                allowed[name] = col_values

        if table == "evidence":
            # exclude_evidence：内存证据行置标记
            target_rows: list[Any] = self._evidences
        else:
            assert table == "research_runs"
            target_rows = self._runs
        count = 0
        for row in target_rows:
            if all(
                (getattr(row, k, None) in vs if len(vs) > 1 else getattr(row, k, None) == vs[0])
                for k, vs in allowed.items()
            ):
                for key, value in values.items():
                    setattr(row, key, value)
                count += 1
        return _UpdateResult(count)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        # 内存会话无事务状态；WS 指令错误路径与 REST 依赖异常处理都会调用
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, RunIntervention):
            self.interventions.append(obj)

    async def refresh(self, _instance: Any) -> None:
        return None


def _client(
    runs: list[ResearchRun],
    registry: RunRegistry,
    *,
    sub_questions: list[SubQuestion] | None = None,
    evidences: list[Evidence] | None = None,
) -> TestClient:
    app = FastAPI(default_response_class=ORJSONResponse)
    app.include_router(runs_router)
    session = _ControlFakeSession([_user()], runs, sub_questions=sub_questions, evidences=evidences)

    async def _fake_db_session():
        yield session

    app.dependency_overrides[api_deps.db_session] = _fake_db_session
    # 暴露内存会话供用例断言介入行
    app.state.test_session = session

    # resume service 经 app.state 取运行依赖；factory 每次返同一个内存会话
    class _Factory:
        def __call__(self) -> Any:
            class _CM:
                async def __aenter__(self_inner) -> _ControlFakeSession:
                    return session

                async def __aexit__(self_inner, *exc: Any) -> None:
                    return None

            return _CM()

    app.state.session_factory = _Factory()
    app.state.checkpointer = None
    app.state.llm = None
    app.state.retrieval_client = None

    # 端点内 get_run_registry 替换为测试注册表（隔离进程单例）
    runs_module.get_run_registry = lambda: registry  # type: ignore[method-assign]

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Any, exc: AppError):
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def auth() -> dict[str, str]:
    token = create_access_token(USER_ID, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def test_pause_running_with_active_task_returns_200(auth: dict[str, str]) -> None:
    """AC-1：running + 在途协程 → 200 paused，任务收到取消信号。"""
    run = _run("running")
    registry = RunRegistry()
    fake_task = _FakeTask()
    registry.register(RUN_ID, fake_task)  # type: ignore[arg-type]
    client = _client([run], registry)
    resp = client.post(f"/runs/{RUN_ID}/pause", json={"reason": "user_action"}, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {"run_id": RUN_ID, "status": "paused", "partial_report_id": None}
    assert run.status == "paused"
    assert fake_task.cancel_calls == 1


def test_pause_already_paused_conflict(auth: dict[str, str]) -> None:
    run = _run("paused")
    registry = RunRegistry()
    client = _client([run], registry)
    resp = client.post(f"/runs/{RUN_ID}/pause", json={}, headers=auth)
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "RUN_ALREADY_PAUSED"


def test_pause_terminal_state_conflict(auth: dict[str, str]) -> None:
    run = _run("succeeded")
    registry = RunRegistry()
    client = _client([run], registry)
    resp = client.post(f"/runs/{RUN_ID}/pause", json={}, headers=auth)
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "RUN_NOT_PAUSABLE"


def test_pause_running_without_active_task_conflict(auth: dict[str, str]) -> None:
    """running 行但注册表无句柄（孤儿）→ 409，不假暂停。"""
    run = _run("running")
    client = _client([run], RunRegistry())
    resp = client.post(f"/runs/{RUN_ID}/pause", json={}, headers=auth)
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "RUN_NOT_PAUSABLE"
    assert run.status == "running"


def test_cancel_running_with_active_task_returns_200(auth: dict[str, str]) -> None:
    """AC-3：running 取消 → 200 cancelled + finished_at + 取消信号。"""
    run = _run("running")
    registry = RunRegistry()
    fake_task = _FakeTask()
    registry.register(RUN_ID, fake_task)  # type: ignore[arg-type]
    client = _client([run], registry)
    resp = client.post(f"/runs/{RUN_ID}/cancel", json={}, headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert fake_task.cancel_calls == 1


def test_cancel_terminal_is_idempotent(auth: dict[str, str]) -> None:
    """succeeded/failed/cancelled 重复 cancel 幂等返回当前状态、不改行。"""
    succeeded = _run("succeeded")
    finished_at = datetime.now(tz=UTC)
    succeeded.finished_at = finished_at
    client = _client([succeeded], RunRegistry())
    resp = client.post(f"/runs/{RUN_ID}/cancel", json={}, headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    assert succeeded.status == "succeeded"
    assert succeeded.finished_at == finished_at


def test_cancel_paused_without_task_finalizes_directly(auth: dict[str, str]) -> None:
    """paused 孤儿取消：无协程可信号，服务端直接置 cancelled（终态帧走 hub 单例）。"""
    run = _run("paused")
    client = _client([run], RunRegistry())
    resp = client.post(
        f"/runs/{RUN_ID}/cancel", json={"reason": "user_action", "keep_partial": False}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert run.status == "cancelled"
    assert run.finished_at is not None


def test_control_requires_auth() -> None:
    run = _run("running")
    client = _client([run], RunRegistry())
    assert client.post(f"/runs/{RUN_ID}/pause", json={}).status_code == 401
    assert client.post(f"/runs/{RUN_ID}/cancel", json={}).status_code == 401


def test_pause_reason_too_long_422(auth: dict[str, str]) -> None:
    run = _run("running")
    registry = RunRegistry()
    registry.register(RUN_ID, _FakeTask())  # type: ignore[arg-type]
    client = _client([run], registry)
    resp = client.post(f"/runs/{RUN_ID}/pause", json={"reason": "x" * 257}, headers=auth)
    assert resp.status_code == 422
    assert run.status == "running"


# ---------------------------------------------------------------------------
# resume 统一入口（AC-2/AC-5 载荷校验矩阵）
# ---------------------------------------------------------------------------


@pytest.fixture
def _neutralize_resume_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """拦截后台 resume_research_async 调度（API 测试不驱动真实图）。

    恢复服务在调度前会写入注册表恢复占位；替身负责释放占位（模拟真实恢复
    协程 register/unregister 生命周期），避免用例间占位残留导致 409。
    """

    async def _no_resume(**kwargs: Any) -> None:
        from app.orchestrator.registry import get_run_registry

        get_run_registry().unregister(kwargs["run_id"])
        return None

    monkeypatch.setattr("app.orchestrator.executor.resume_research_async", _no_resume)


def test_resume_non_paused_conflict(auth: dict[str, str], _neutralize_resume_dispatch: None) -> None:
    run = _run("running")
    client = _client([run], RunRegistry())
    resp = client.post(f"/runs/{RUN_ID}/resume", json={}, headers=auth)
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "RUN_NOT_RESUMABLE"


def test_resume_manual_pause_proceed_returns_200(
    auth: dict[str, str], _neutralize_resume_dispatch: None
) -> None:
    """手动软暂停（checkpoint 无 interrupt_reason）：空 body 即纯继续。"""
    run = _run("paused")
    client = _client([run], RunRegistry())
    resp = client.post(f"/runs/{RUN_ID}/resume", json={}, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {"run_id": RUN_ID, "status": "running", "partial_report_id": None}
    assert run.status == "running"


def test_resume_human_input_branches_mutually_exclusive_422(
    auth: dict[str, str], _neutralize_resume_dispatch: None
) -> None:
    run = _run("paused")
    client = _client([run], RunRegistry())
    resp = client.post(
        f"/runs/{RUN_ID}/resume",
        json={"human_input": {"kind": "proceed", "answers": {"scope": "近一年"}}},
        headers=auth,
    )
    assert resp.status_code == 422


def test_resume_clarify_gate_requires_answers(
    auth: dict[str, str], monkeypatch: pytest.MonkeyPatch, _neutralize_resume_dispatch: None
) -> None:
    """澄清挂起点纯继续 422；带非空 answers 200。"""

    async def _read_clarify(**kwargs: Any) -> dict[str, Any]:
        return {
            "reason": "clarify",
            "payload": {"questions": [{"key": "scope"}], "defaults": {}},
        }

    monkeypatch.setattr("app.orchestrator.executor.read_run_interrupt", _read_clarify)

    run = _run("paused")
    client = _client([run], RunRegistry())
    resp_empty = client.post(f"/runs/{RUN_ID}/resume", json={}, headers=auth)
    assert resp_empty.status_code == 422

    run.status = "paused"  # 行内复位（422 不改状态；此处显式保险）
    resp_answers = client.post(
        f"/runs/{RUN_ID}/resume",
        json={"human_input": {"answers": {"scope": "近三年"}}},
        headers=auth,
    )
    assert resp_answers.status_code == 200
    assert resp_answers.json()["status"] == "running"


def test_resume_action_rejected_before_t3(
    auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    _neutralize_resume_dispatch: None,
) -> None:
    """action 分支在 T3 介入队列落地前返回 409 INTERVENE_NOT_ALLOWED。"""

    async def _read_manual(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.orchestrator.executor.read_run_interrupt", _read_manual)

    run = _run("paused")
    client = _client([run], RunRegistry())
    resp = client.post(
        f"/runs/{RUN_ID}/resume",
        json={"human_input": {"action": {"type": "exclude_evidence", "payload": {"evidence_id": "ev-1"}}}},
        headers=auth,
    )
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "INTERVENE_NOT_ALLOWED"
    assert run.status == "paused"


# ---------------------------------------------------------------------------
# intervene 主动介入（AC-8/AC-9/AC-10）
# ---------------------------------------------------------------------------


def _subq(sq_id: str = "sq-1") -> SubQuestion:
    return SubQuestion(
        id=sq_id,
        run_id=RUN_ID,
        question="原问题",
        depends_on=[],
        status="running",
        evidence_count=0,
    )


def _evidence(ev_id: str = "ev-1") -> Evidence:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    return Evidence(
        id=ev_id,
        run_id=RUN_ID,
        sub_question_id="sq-1",
        url=f"https://example.gov/{ev_id}",
        domain="example.gov",
        title=f"证据-{ev_id}",
        snippet="摘要",
        content=None,
        source_type="official_doc",
        source_level="primary",
        credibility="A",
        relevance_score=0.9,
        fingerprint=f"fp-{ev_id}",
        published_at=now,
        fetched_at=now,
        excluded_by_user=False,
        created_at=now,
        updated_at=now,
    )


def test_intervene_ask_followup_running_retrieve_accepted(auth: dict[str, str]) -> None:
    """AC-8：running@retrieve + 本 run 子问题 → 200，入队 pending。"""
    run = _run("running")
    run.current_stage = "retrieve"
    client = _client([run], RunRegistry(), sub_questions=[_subq()])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={
            "type": "ask_followup",
            "payload": {"sub_question_id": "sq-1", "question": "补充追问近三年数据"},
        },
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    stored = client.app.state.test_session.interventions
    assert len(stored) == 1
    assert stored[0].type == "ask_followup"
    assert stored[0].status == "pending"
    assert stored[0].payload["question"] == "补充追问近三年数据"


def test_intervene_ask_followup_wrong_stage_conflict(auth: dict[str, str]) -> None:
    run = _run("running")
    run.current_stage = "decompose"
    client = _client([run], RunRegistry(), sub_questions=[_subq()])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "ask_followup", "payload": {"sub_question_id": "sq-1", "question": "x"}},
        headers=auth,
    )
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "INTERVENE_NOT_ALLOWED"
    assert client.app.state.test_session.interventions == []


def test_intervene_ask_followup_unknown_subquestion_404(auth: dict[str, str]) -> None:
    run = _run("running")
    run.current_stage = "retrieve"
    client = _client([run], RunRegistry(), sub_questions=[_subq()])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "ask_followup", "payload": {"sub_question_id": "sq-other", "question": "x"}},
        headers=auth,
    )
    assert resp.status_code == 404


def test_intervene_ask_followup_blank_question_422(auth: dict[str, str]) -> None:
    run = _run("running")
    run.current_stage = "retrieve"
    client = _client([run], RunRegistry(), sub_questions=[_subq()])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "ask_followup", "payload": {"sub_question_id": "sq-1", "question": "  "}},
        headers=auth,
    )
    assert resp.status_code == 422


def test_intervene_exclude_evidence_marks_db_applied(auth: dict[str, str]) -> None:
    """AC-9：剔除同步置 excluded_by_user=true，介入行直接记 applied。"""
    run = _run("running")
    run.current_stage = "critique"
    evidence = _evidence()
    client = _client([run], RunRegistry(), evidences=[evidence])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "exclude_evidence", "payload": {"evidence_id": "ev-1"}},
        headers=auth,
    )
    assert resp.status_code == 200
    assert evidence.excluded_by_user is True
    stored = client.app.state.test_session.interventions
    assert len(stored) == 1
    assert stored[0].type == "exclude_evidence"
    assert stored[0].status == "applied"


def test_intervene_exclude_at_report_stage_conflict(auth: dict[str, str]) -> None:
    """报告阶段起不接受剔除（成品报告走 M4 doubt 通道）。"""
    run = _run("running")
    run.current_stage = "report"
    evidence = _evidence()
    client = _client([run], RunRegistry(), evidences=[evidence])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "exclude_evidence", "payload": {"evidence_id": "ev-1"}},
        headers=auth,
    )
    assert resp.status_code == 409
    assert evidence.excluded_by_user is False


def test_intervene_exclude_unknown_evidence_404(auth: dict[str, str]) -> None:
    run = _run("running")
    run.current_stage = "retrieve"
    client = _client([run], RunRegistry(), evidences=[_evidence()])
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "exclude_evidence", "payload": {"evidence_id": "ev-missing"}},
        headers=auth,
    )
    assert resp.status_code == 404


def test_intervene_requires_running(auth: dict[str, str]) -> None:
    run = _run("succeeded")
    client = _client([run], RunRegistry())
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "exclude_evidence", "payload": {"evidence_id": "ev-1"}},
        headers=auth,
    )
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "INTERVENE_NOT_ALLOWED"


def test_intervene_unknown_action_type_422(auth: dict[str, str]) -> None:
    run = _run("running")
    run.current_stage = "retrieve"
    client = _client([run], RunRegistry())
    resp = client.post(
        f"/runs/{RUN_ID}/intervene",
        json={"type": "revert_stage", "payload": {"stage": "retrieve"}},
        headers=auth,
    )
    assert resp.status_code == 422


def test_intervene_idempotency_key_dedupes(auth: dict[str, str]) -> None:
    """AC-10：同一 Idempotency-Key 双击只入队一次。"""
    run = _run("running")
    run.current_stage = "retrieve"
    client = _client([run], RunRegistry(), sub_questions=[_subq()])
    body = {"type": "ask_followup", "payload": {"sub_question_id": "sq-1", "question": "x"}}
    headers = {**auth, "Idempotency-Key": "key-001"}
    first = client.post(f"/runs/{RUN_ID}/intervene", json=body, headers=headers)
    second = client.post(f"/runs/{RUN_ID}/intervene", json=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(client.app.state.test_session.interventions) == 1
    assert client.app.state.test_session.interventions[0].idempotency_key == "key-001"
