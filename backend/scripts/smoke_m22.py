"""M2-2 真实链路冒烟脚本（TR-11.2 证据）。

用法（backend 目录，需 .env 配置真实 DeepSeek 密钥与可连通的 Postgres）：

    TEST_DATABASE_URL=postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research \
    uv run python scripts/smoke_m22.py

链路范围：
- 真实 AsyncPostgresSaver（独立 schema m22_smoke，可重复执行）；
- 真实 DeepSeek（LLMClient.from_registry，温度 0）承担 clarify/decompose/critique；
- 检索使用固定的高对立样本（离线），保证 high 冲突稳定出现（真实博查检索不保证
  同一时点返回对立结论，故按任务书允许的种子 run 方式补证）；
- 分歧三端点经真实 FastAPI 路由（含鉴权依赖）+ httpx ASGI 调用；
- 末条 both 裁决后真实后台恢复至 succeeded，打印报告「冲突与不确定性」段。

任何关键断言失败以非 0 退出码结束。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# 复用 tests/conftest.py 的独立 schema 建表辅助（直接运行无 pytest 路径注入）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from conftest import create_all_in_schema  # type: ignore[import-not-found]
from httpx import ASGITransport, AsyncClient
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps as api_deps
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.base import new_ulid
from app.db.models import Project, ResearchRun, Team, User
from app.orchestrator.executor import run_research_async
from app.provider.client import LLMClient
from app.realtime.hub import RealtimeHub
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient

_SCHEMA = "m22_smoke"
_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://deep_research:deep_research@localhost:5432/deep_research",
)
_PSYCOPG_DSN = _DSN.replace("+asyncpg", "", 1)


class _SmokeRetrieval(RetrievalClient):
    """固定高对立样本：两议题各自给出互斥口径，真实 LLM 应判 high 冲突。"""

    def __init__(self) -> None:
        pass

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:  # noqa: ARG002
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="国家统计局发布光伏新增装机规模数据",
                url="https://stats.gov.cn/pv-smoke",
                snippet="光伏新增装机规模达到二百一十吉瓦同比大增四成五",
                score=0.95,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="行业协会披露光伏新增装机规模口径",
                url="https://assoc.org.cn/pv-smoke",
                snippet="光伏新增装机规模按并网口径仅一百二十吉瓦差距明显",
                score=0.9,
            ),
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(hits)


def _log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


async def main() -> int:
    engine = create_async_engine(
        _DSN,
        connect_args={"server_settings": {"search_path": f"{_SCHEMA},public"}},
    )

    # 先建独立 schema，再让 saver.setup() 在其中建 checkpoint 表
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {_SCHEMA}"))

    pool: Any = AsyncConnectionPool(
        _PSYCOPG_DSN,
        open=False,
        kwargs={"autocommit": True, "options": f"-c search_path={_SCHEMA}"},
    )
    await pool.open(wait=True)
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    saver = AsyncPostgresSaver(pool)
    await saver.setup()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(create_all_in_schema, _SCHEMA)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        # 种子团队/用户/项目/run
        async with maker() as session:
            team = Team(id=new_ulid(), name="M2-2 冒烟团队", plan="free", settings={})
            user = User(
                id=new_ulid(),
                team_id=team.id,
                email=f"smoke-{new_ulid().lower()}@example.com",
                hashed_password="x",
                display_name="冒烟用户",
                role="owner",
            )
            project = Project(
                id=new_ulid(),
                team_id=team.id,
                owner_id=user.id,
                name="M2-2 冒烟项目",
                status="active",
            )
            run = ResearchRun(
                id=new_ulid(),
                project_id=project.id,
                creator_id=user.id,
                template_id="generic",
                tier="standard",
                question="2026 年中国光伏新增装机规模各方口径是否冲突",
                token_budget=100_000,
            )
            session.add_all([team, user, project, run])
            await session.commit()
            run_id = run.id
            user_id = user.id

        hub = RealtimeHub()
        llm = LLMClient.from_registry()

        # 真实首次执行（真实 LLM 全节点 + 离线固定对立样本）
        _log("首次执行：真实 DeepSeek + 真实 PostgresSaver")
        await run_research_async(
            run_id=run_id,
            project_id=run.project_id,
            template_id="generic",
            tier="standard",
            question=run.question,
            token_budget=100_000,
            clarification=None,
            team_id=team.id,
            creator_id=user_id,
            trace_id=f"smoke-{run_id}",
            session_factory=maker,
            llm=llm,
            retrieval_client=_SmokeRetrieval(),
            hub=hub,
            checkpointer=saver,
        )
        async with maker() as session:
            paused = await session.get(ResearchRun, run_id)
            assert paused is not None and paused.status == "paused", (
                f"期望 paused，实际 {paused.status if paused else None}"
            )
            _log(f"run 已挂起：status={paused.status} stage={paused.current_stage}")

        # 真实 FastAPI 应用（含 AppError 处理器）+ JWT 鉴权
        from app.main import create_app

        app = create_app()
        app.state.session_factory = maker
        app.state.hub = hub
        app.state.llm = llm
        app.state.retrieval_client = _SmokeRetrieval()
        app.state.checkpointer = saver

        async def _override_session() -> AsyncIterator[AsyncSession]:
            async with maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[api_deps.db_session] = _override_session
        headers = {"Authorization": f"Bearer {create_access_token(user_id, settings=get_settings())}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            list_resp = await client.get(f"/api/v1/runs/{run_id}/conflicts", headers=headers)
            assert list_resp.status_code == 200, list_resp.text
            conflicts = list_resp.json()
            _log(f"GET 列表 200：{len(conflicts)} 条冲突")
            assert conflicts, "真实 LLM 未检出冲突，样本/模型异常"
            for item in conflicts:
                _log(
                    f"  - {item['id']} type={item['type']} severity={item['severity']} "
                    f"status={item['status']} claim={item['claim']}"
                )

            detail = await client.get(f"/api/v1/conflicts/{conflicts[0]['id']}", headers=headers)
            assert detail.status_code == 200, detail.text
            body = detail.json()
            assert set(body["evidence_a"]) == {
                "id",
                "title",
                "url",
                "domain",
                "snippet",
                "credibility",
                "source_type",
                "published_at",
            }
            _log(
                f"GET 详情 200：evidence_a.title={body['evidence_a']['title']}；"
                f"evidence_b.title={body['evidence_b']['title']}"
            )

            # 鉴权失败快查
            anon = await client.get(f"/api/v1/runs/{run_id}/conflicts")
            assert anon.status_code == 401, anon.status_code
            _log("无 token 列表 401 OK")

            # 全部以 both 裁决；仅对 high/awaiting_human 提交
            targets = [c for c in conflicts if c["status"] == "awaiting_human"]
            assert targets, "没有待裁决冲突"
            for idx, item in enumerate(targets, start=1):
                resp = await client.post(
                    f"/api/v1/conflicts/{item['id']}/verdict",
                    headers=headers,
                    json={
                        "choice": "both",
                        "reason": "双方统计口径不同，观点并存",
                        "additional_note": "引用结论时必须注明口径差异",
                    },
                )
                assert resp.status_code == 200, resp.text
                _log(f"POST 裁决 {idx}/{len(targets)} 200：{resp.json()}")

            # 轮询恢复结果（真实后台任务）
            deadline = asyncio.get_event_loop().time() + 60
            final: ResearchRun | None = None
            while asyncio.get_event_loop().time() < deadline:
                async with maker() as session:
                    final = await session.get(ResearchRun, run_id)
                if final is not None and final.status in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.5)
            assert final is not None and final.status == "succeeded", (
                f"恢复后期望 succeeded，实际 {final.status if final else None}"
            )
            _log(f"自动恢复完成：status={final.status} stage={final.current_stage}")

            async with maker() as session:
                report_md = (
                    await session.execute(
                        text("SELECT content_md FROM reports WHERE run_id = :rid").bindparams(rid=run_id)
                    )
                ).scalar_one()

        marker = "## 冲突与不确定性"
        section = report_md[report_md.index(marker) :]
        next_h = section.find("\n## ", 2)
        section = section if next_h == -1 else section[:next_h]
        _log("报告「冲突与不确定性」段：\n" + section.strip())
        assert "分歧与局限" in report_md
        assert "双方观点并存" in report_md
        _log("冒烟全部通过")
        return 0
    finally:
        await pool.close()
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
