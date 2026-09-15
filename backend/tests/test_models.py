"""ORM 模型注册与 ULID 主键生成烟测（不连 DB，仅验证元数据与默认工厂）。

真实多租户隔离 + 外键约束验证留给 M2（需 Postgres + 测试库）。
"""

from __future__ import annotations

from app.db import models
from app.db.base import Base, new_ulid
from app.db.models.identity import Team, User
from app.db.models.project import Project


def test_all_models_registered() -> None:
    """全部核心模型应注册到 ``Base.metadata``。"""
    expected = {
        "teams",
        "users",
        "projects",
        "research_runs",
        "stages",
        "sub_questions",
        "evidence",
        "conflicts",
        "verdicts",
        "reports",
        "report_citations",
        "knowledge_items",
        "knowledge_embeddings",
        "audit_entries",
        "run_interventions",
        "telemetry_events",
    }
    actual = set(Base.metadata.tables.keys())
    assert expected <= actual, f"缺失表：{expected - actual}"


def test_user_carries_team_id() -> None:
    """多租户基线：``User`` 表必须含 ``team_id`` 列与索引。"""
    table = User.__table__
    cols = {c.name for c in table.columns}
    assert "team_id" in cols
    assert "email" in cols
    assert any({"team_id"} <= {c.name for c in i.columns} for i in table.indexes)


def test_project_team_index_composite() -> None:
    """``projects`` 应存在 ``(team_id, status)`` 复合索引（LLD §5.3.3）。"""
    table = Project.__table__
    composite = {tuple(i.columns.keys()) for i in table.indexes}
    assert ("team_id", "status") in composite


def test_team_settings_default_is_dict() -> None:
    """``Team.settings`` 列默认应为 ``dict``（LLD §5.3.1）。"""
    table = Team.__table__
    col = table.columns["settings"]
    assert col.default is not None, "settings 必须有列级默认值"
    # SQLAlchemy 把 ``default=dict`` 包装为 ``CallableColumnDefault``；
    # ``arg`` 是 dict 的工厂（语义上等价 ``dict()``）。直接断言类型与可调用性。
    arg = col.default.arg
    assert callable(arg), f"default 应可调用，实际类型={type(arg).__name__}"
    assert arg is dict or getattr(arg, "__name__", "") == "dict", (
        f"default 应源自 dict 工厂，实际={arg!r}"
    )


def test_new_ulid_is_26_chars() -> None:
    """LLD §5.1：主键生成器应产出 26 字符 ULID。"""
    u1 = new_ulid()
    u2 = new_ulid()
    assert len(u1) == 26
    assert len(u2) == 26
    assert u1 != u2


def test_modules_import_cleanly() -> None:
    """冒烟：模型 import 不抛 + 导出列表完整。"""
    assert models.Team is Team
    assert models.User is User
    assert models.Project is Project
