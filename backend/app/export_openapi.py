"""导出 OpenAPI schema 到 docs/contract/。

用法：
    uv run python -m app.export_openapi              # 仅导出当前里程碑快照
    uv run python -m app.export_openapi --refresh-m1 # 同时重写已冻结的 M1 快照

M1 快照（openapi-m1.json）为历史冻结文件，默认不覆盖：随里程碑演进，应用的
components.schemas 会持续增加，重生成会让 M1 文件产生与端点无关的噪音 diff。
M2-2 快照（openapi-m2-2.json，M1 子集 + M2-1 意图/闲聊 + conflicts 三端点）为当前导出品；
快照为累积超集，每个里程碑批次必须把已交付端点全部纳入，禁止选择性白名单。

环境变量需与 conftest.py 的 _REQUIRED_ENV 对齐（测试环境最小变量集）。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

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
_M1_ENDPOINTS = frozenset(
    {
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
    }
)

# M2 阶段在 M1 之上新增的端点（累积快照：含已交付的全部里程碑，禁止选择性白名单）
# - M2-1 意图路由与闲聊：/intent/classify、/assistant/chat（SSE 流，路径仍纳入 OpenAPI）
# - M2-2 批判收敛与人机裁决：分歧三端点（FR-6/7/8）
_M22_ENDPOINTS = frozenset(
    {
        "/api/v1/intent/classify",
        "/api/v1/assistant/chat",
        "/api/v1/runs/{run_id}/conflicts",
        "/api/v1/conflicts/{conflict_id}",
        "/api/v1/conflicts/{conflict_id}/verdict",
    }
)

_M22_DESCRIPTION = (
    "本 schema 为截至 M2-2（批判收敛与人机裁决）后端冻结契约的累积快照，"
    "包含 M1 端点 + M2-1 意图路由与闲聊 + M2-2 分歧三端点。\n"
    "M2-1：POST /intent/classify 判别意图（chat/research/uncertain，force 手动强制，"
    "故障保守降级 research）；POST /assistant/chat 闲聊 SSE 流式直答（不检索、不落项目数据）。\n"
    "M2-2：run 下分歧列表（创建者归属，401/404）、分歧详情（内嵌双方证据八项摘要）、"
    "提交裁决（409 重复裁决/422 枚举与非空校验）；冲突 type 四值 "
    "factual/methodological/temporal/perspective，severity 三值 low/medium/high。\n"
    "WebSocket 帧 conflict.detected/conflict.verdicts 不在 OpenAPI paths 中，"
    "其载荷契约见 M2-2 阶段技术方案。"
)


def _filter_paths(schema: dict[str, Any], whitelist: frozenset[str]) -> dict[str, Any]:
    paths = cast(dict[str, Any], schema.get("paths", {}))
    return {path: spec for path, spec in paths.items() if path in whitelist}


def _write_snapshot(
    schema: dict[str, Any],
    paths: dict[str, Any],
    *,
    filename: str,
    title: str,
    description: str,
    output_dir: Path,
) -> None:
    info = cast(dict[str, Any], schema.get("info", {}))
    snapshot: dict[str, Any] = {
        **schema,
        "paths": paths,
        "info": {**info, "title": title, "description": description},
    }
    output_path = output_dir / filename
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    print(f"已导出：{output_path}（端点数 {len(paths)}）")


def main() -> None:
    """导出 M2-2 冻结契约；--refresh-m1 时同步重写 M1 历史快照。"""
    parser = argparse.ArgumentParser(description="导出 OpenAPI 冻结快照")
    parser.add_argument(
        "--refresh-m1",
        action="store_true",
        help="同时重写已冻结的 openapi-m1.json（默认跳过以保持历史快照稳定）",
    )
    args = parser.parse_args()

    # 注入最小环境变量
    for key, value in _REQUIRED_ENV.items():
        os.environ.setdefault(key, value)

    from app.main import app

    schema = app.openapi()

    output_dir = Path(__file__).resolve().parents[2] / "docs" / "contract"
    output_dir.mkdir(parents=True, exist_ok=True)

    m1_paths = _filter_paths(schema, _M1_ENDPOINTS)
    m22_paths = _filter_paths(schema, _M1_ENDPOINTS | _M22_ENDPOINTS)

    m1_output = output_dir / "openapi-m1.json"
    if args.refresh_m1 or not m1_output.exists():
        _write_snapshot(
            schema,
            m1_paths,
            filename="openapi-m1.json",
            title="AI 研究者助手 · M1 联调接口契约（冻结）",
            description=(
                "本 schema 为 M1 里程碑前后端联调的冻结契约。\n"
                "仅包含 M1 已实现的 REST 端点；WebSocket 端点 "
                "/api/v1/ws/runs/{run_id}/stream 不在 OpenAPI paths 中，"
                "其契约见后端详细设计 §4.3.1。\n"
                "占位端点（users/teams/conflicts/knowledge/connectors/templates/audit）"
                "不属于 M1 联调范围，已从本契约中排除。"
            ),
            output_dir=output_dir,
        )
    else:
        print("跳过已冻结的 M1 快照（如需重写加 --refresh-m1）")

    _write_snapshot(
        schema,
        m22_paths,
        filename="openapi-m2-2.json",
        title="AI 研究者助手 · M2-2 累积接口契约冻结（M1 + M2-1 + M2-2）",
        description=_M22_DESCRIPTION,
        output_dir=output_dir,
    )

    all_paths = cast(dict[str, Any], schema.get("paths", {}))
    placeholder_count = len(all_paths) - len(m22_paths)
    print(f"未纳入任何快照的占位/其他端点数：{placeholder_count}")
    print("M2-2 新增端点：")
    for path in sorted(_M22_ENDPOINTS):
        methods = sorted(m22_paths[path].keys())
        print(f"  {path}  [{', '.join(methods).upper()}]")


if __name__ == "__main__":
    main()
