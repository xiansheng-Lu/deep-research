"""导出 OpenAPI schema 到 docs/contract/。

用法：
    uv run python -m app.export_openapi              # 仅导出当前里程碑快照
    uv run python -m app.export_openapi --refresh-m1 # 同时重写已冻结的 M1 快照

M1 快照（openapi-m1.json）为历史冻结文件，默认不覆盖：随里程碑演进，应用的
components.schemas 会持续增加，重生成会让 M1 文件产生与端点无关的噪音 diff。
M2-2/M2-4/M2-5 快照均已冻结，默认跳过（分别加 --refresh-m22/--refresh-m24/
--refresh-m25 重写）；M2-7 快照（openapi-m2-7.json）为当前导出品
（M1 + M2-1 + M2-2 + M2-4 + M2-5 + M2-7 数据点级溯源，25 路径）。
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

# M2-4 在 M2-2 之上新增的看板只读端点（实时成本 + 看板，FR：run 详情六类视图）
# - GET /runs 随白名单路径自动带上（POST 创建已在 M1 白名单中，按路径整体纳入）
_M24_ENDPOINTS = frozenset(
    {
        "/api/v1/runs/{run_id}/stages",
        "/api/v1/runs/{run_id}/sub-questions",
        "/api/v1/runs/{run_id}/evidence",
        "/api/v1/runs/{run_id}/evidence/{evidence_id}",
        "/api/v1/runs/{run_id}/cost/snapshot",
    }
)

# M2-5 在 M2-4 之上新增的运行控制四端点（pause/resume/cancel/intervene）
_M25_ENDPOINTS = frozenset(
    {
        "/api/v1/runs/{run_id}/pause",
        "/api/v1/runs/{run_id}/resume",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/intervene",
    }
)

# M2-7 在 M2-5 之上新增的数据点级溯源端点（报告级信源索引）
_M27_ENDPOINTS = frozenset(
    {
        "/api/v1/reports/{run_id}/citations",
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

_M24_DESCRIPTION = (
    "本 schema 为截至 M2-4（实时成本与看板接口）后端冻结契约的累积快照，"
    "在 M2-2 全部端点之上新增五个 run 详情只读 GET："
    "阶段时间线（stages，按固定六阶段顺序返回状态/尝试次数/时间戳/token）、"
    "子问题列表（sub-questions，状态与证据计数）、证据池分页（evidence，"
    "默认剔除用户排除项，include_excluded=true 可带出）、证据详情（全文 content）、"
    "成本快照（cost/snapshot，预算占用比例与 danger/warning 分级）。\n"
    "列表统一扁平分页信封 {items,total,page,page_size,has_more}，page_size 上限 100；"
    "归属校验与创建端点同一不泄漏口径（不存在/非创建者统一 404）。\n"
    "WebSocket 帧 stage.started/stage.finished/stage.failed、"
    "sub_question.created/started/finished、evidence.fetched、"
    "token.usage.update 不在 OpenAPI paths 中，其载荷契约见 M2-3/M2-4 阶段技术方案。"
)

_M25_DESCRIPTION = (
    "本 schema 为截至 M2-5（用户介入接口）后端冻结契约的累积快照，"
    "在 M2-4 全部端点之上新增四个运行控制 POST："
    "pause（软暂停，仅 running 且有在途协程受理，409 RUN_NOT_PAUSABLE/"
    "RUN_ALREADY_PAUSED）、resume（统一人类输入入口，human_input 的 answers/"
    "action/kind 三选一互斥；澄清挂起必须带非空 answers，409 RUN_NOT_RESUMABLE）、"
    "cancel（硬取消终态，对终态幂等返回当前状态，可带回 partial_report_id）、"
    "intervene（running 中 ask_followup 仅 retrieve、exclude_evidence 限 "
    "retrieve/standardize/critique；409 INTERVENE_NOT_ALLOWED、"
    "422 INVALID_ACTION_PAYLOAD，Idempotency-Key 双击幂等）。\n"
    "统一响应 RunControlResponse {run_id,status,partial_report_id?}；"
    "RunResponse 新增可选 interrupt 字段（paused@clarify 时携带 "
    "reason/questions/defaults/expires_in_seconds，为 M2-4 契约的超集增量）。\n"
    "WebSocket 客户端指令 intervene/cancel 与应答 intervene.ack/intervene.error、"
    "实时帧 interrupt.requested、run.finished(status=paused/cancelled) 不在 "
    "OpenAPI paths 中，其载荷契约见 M2-5 阶段技术方案。"
)

_M27_DESCRIPTION = (
    "本 schema 为截至 M2-7（数据点级溯源）后端冻结契约的累积快照，"
    "在 M2-5 全部端点之上新增一个报告只读端点：\n"
    "GET /reports/{run_id}/citations 返回报告级信源索引（按 marker [N] 升序、"
    "报告级去重），元素为 ReportCitationItem：内联三字段 evidence_id/marker/snippet "
    "之外补七项回溯展示字段 url/title/domain/source_type/source_level/credibility/"
    "published_at（marker/snippet 与该七项合计九展示字段，published_at 可空）。\n"
    "既有 GET /reports/{run_id} 与 GET /runs/{run_id}/report 响应在原八字段之上增 "
    "outline（sec-overview/sec-findings/sec-disputes/sec-limitations 语义章节，"
    "分歧/局限章按需出现）与 blocks（conclusion/evidence/dispute/limitation 四类，"
    "conclusion/dispute 带 claim_id 与 confidence，dispute 带 conflict_id，"
    "block 内联 citations 为 {evidence_id, marker, snippet}）；draft/历史报告两数组为空。\n"
    "终稿 status=final，含数字/事实声明的论断 100% 绑定信源（缺源降级 inferred，"
    "无来源数字块经一次自修复仍不达标则剔除）；同一证据跨块共享 marker，"
    "未被引用证据不占号。WebSocket 帧在本批零变化。"
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
    """导出 M2-7 冻结契约；历史快照默认跳过，加对应 --refresh 重写。"""
    parser = argparse.ArgumentParser(description="导出 OpenAPI 冻结快照")
    parser.add_argument(
        "--refresh-m1",
        action="store_true",
        help="同时重写已冻结的 openapi-m1.json（默认跳过以保持历史快照稳定）",
    )
    parser.add_argument(
        "--refresh-m22",
        action="store_true",
        help="同时重写已冻结的 openapi-m2-2.json（默认跳过以保持历史快照稳定）",
    )
    parser.add_argument(
        "--refresh-m24",
        action="store_true",
        help="同时重写已冻结的 openapi-m2-4.json（默认跳过以保持快照稳定）",
    )
    parser.add_argument(
        "--refresh-m25",
        action="store_true",
        help="同时重写已冻结的 openapi-m2-5.json（M2-7 起默认跳过以保持快照稳定）",
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
    m24_paths = _filter_paths(schema, _M1_ENDPOINTS | _M22_ENDPOINTS | _M24_ENDPOINTS)
    m25_paths = _filter_paths(schema, _M1_ENDPOINTS | _M22_ENDPOINTS | _M24_ENDPOINTS | _M25_ENDPOINTS)
    m27_paths = _filter_paths(
        schema,
        _M1_ENDPOINTS | _M22_ENDPOINTS | _M24_ENDPOINTS | _M25_ENDPOINTS | _M27_ENDPOINTS,
    )

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

    m22_output = output_dir / "openapi-m2-2.json"
    if args.refresh_m22 or not m22_output.exists():
        _write_snapshot(
            schema,
            m22_paths,
            filename="openapi-m2-2.json",
            title="AI 研究者助手 · M2-2 累积接口契约冻结（M1 + M2-1 + M2-2）",
            description=_M22_DESCRIPTION,
            output_dir=output_dir,
        )
    else:
        print("跳过已冻结的 M2-2 快照（如需重写加 --refresh-m22）")

    m24_output = output_dir / "openapi-m2-4.json"
    if args.refresh_m24 or not m24_output.exists():
        _write_snapshot(
            schema,
            m24_paths,
            filename="openapi-m2-4.json",
            title="AI 研究者助手 · M2-4 累积接口契约冻结（M1 + M2-1 + M2-2 + M2-4）",
            description=_M24_DESCRIPTION,
            output_dir=output_dir,
        )
    else:
        print("跳过已冻结的 M2-4 快照（如需重写加 --refresh-m24）")

    m25_output = output_dir / "openapi-m2-5.json"
    if args.refresh_m25 or not m25_output.exists():
        _write_snapshot(
            schema,
            m25_paths,
            filename="openapi-m2-5.json",
            title="AI 研究者助手 · M2-5 累积接口契约冻结（M1 + M2-1 + M2-2 + M2-4 + M2-5）",
            description=_M25_DESCRIPTION,
            output_dir=output_dir,
        )
    else:
        print("跳过已冻结的 M2-5 快照（如需重写加 --refresh-m25）")

    _write_snapshot(
        schema,
        m27_paths,
        filename="openapi-m2-7.json",
        title="AI 研究者助手 · M2-7 累积接口契约冻结（M1 + M2-1 + M2-2 + M2-4 + M2-5 + M2-7）",
        description=_M27_DESCRIPTION,
        output_dir=output_dir,
    )

    all_paths = cast(dict[str, Any], schema.get("paths", {}))
    placeholder_count = len(all_paths) - len(m27_paths)
    print(f"未纳入任何快照的占位/其他端点数：{placeholder_count}")
    print("M2-7 新增端点：")
    for path in sorted(_M27_ENDPOINTS):
        methods = sorted(m27_paths[path].keys())
        print(f"  {path}  [{', '.join(methods).upper()}]")


if __name__ == "__main__":
    main()
