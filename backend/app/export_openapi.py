"""导出 OpenAPI schema 到 docs/contract/openapi-m1.json。

用法：
    uv run python -m app.export_openapi

环境变量需与 conftest.py 的 _REQUIRED_ENV 对齐（测试环境最小变量集）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# 测试环境最小变量集（与 conftest.py 对齐）
_REQUIRED_ENV: dict[str, str] = {
    "APP_ENV": "dev",
    "SECRET_KEY": "contract-freeze-key-do-not-use-in-prod",
    "DB_ASYNC_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "DB_SYNC_URL": "postgresql://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "CELERY_BROKER_URL": "redis://localhost:6379/0",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
    "OBJECT_STORAGE_ENDPOINT": "localhost:9000",
    "OBJECT_STORAGE_ACCESS_KEY": "test",
    "OBJECT_STORAGE_SECRET_KEY": "test-secret",
    "OBJECT_STORAGE_BUCKET": "test-bucket",
}

# M1 联调接口子集白名单（不含 WS，FastAPI 不把 WS 纳入 OpenAPI paths）
_M1_ENDPOINTS = frozenset({
    "/healthz",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/api/v1/projects",
    "/api/v1/runs",
    "/api/v1/runs/{run_id}",
    "/api/v1/runs/{run_id}/report",
    "/api/v1/reports/{run_id}",
})


def main() -> None:
    """导出 OpenAPI schema 到 docs/contract/openapi-m1.json。"""
    # 注入最小环境变量
    for key, value in _REQUIRED_ENV.items():
        os.environ.setdefault(key, value)

    from app.main import app

    schema = app.openapi()

    # 标注 M1 冻结子集
    m1_paths: dict[str, object] = {}
    placeholder_paths: dict[str, object] = {}
    for path, spec in schema.get("paths", {}).items():
        if path in _M1_ENDPOINTS:
            m1_paths[path] = spec
        else:
            placeholder_paths[path] = spec

    # 构造 M1 冻结契约：只包含 M1 子集
    m1_schema = {
        **schema,
        "paths": m1_paths,
        "info": {
            **schema.get("info", {}),
            "title": "AI 研究者助手 · M1 联调接口契约（冻结）",
            "description": (
                "本 schema 为 M1 里程碑前后端联调的冻结契约。\n"
                "仅包含 M1 已实现的 REST 端点；WebSocket 端点 "
                "/api/v1/ws/runs/{run_id}/stream 不在 OpenAPI paths 中，"
                "其契约见后端详细设计 §4.3.1。\n"
                "占位端点（users/teams/conflicts/knowledge/connectors/templates/audit）"
                "不属于 M1 联调范围，已从本契约中排除。"
            ),
        },
    }

    # 输出路径：docs/contract/openapi-m1.json
    output_dir = Path(__file__).resolve().parents[2] / "docs" / "contract"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "openapi-m1.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(m1_schema, f, ensure_ascii=False, indent=2)

    print(f"OpenAPI M1 契约已导出：{output_path}")
    print(f"M1 端点数：{len(m1_paths)}")
    print(f"占位端点数（已排除）：{len(placeholder_paths)}")
    print("M1 端点列表：")
    for path in sorted(m1_paths.keys()):
        methods = sorted(m1_paths[path].keys())  # type: ignore[union-attr]
        print(f"  {path}  [{', '.join(methods).upper()}]")


if __name__ == "__main__":
    main()
