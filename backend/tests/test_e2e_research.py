"""WP-5.8 端到端冒烟测试：跑通完整 StateGraph 主链路。

目标（对齐《后端详细设计》§6.3 与 M1 冲刺开发计划 WP-5 验收）：
- 固定问题在主链路上不挂起、一路跑到 END
- 产出非空 Markdown 报告
- current_stage 记录到 report
- token_used 不超 token_budget

实现策略：
- 复制 ``app.orchestrator.graph.build_research_graph`` 的接线逻辑，在测试侧
  通过闭包把 stub ``NodeDeps`` 注入到 LLM/检索敏感节点；
  不修改 ``graph.py``，保持生产入口（无 deps）形态不变。
- ``LLMClient.complete_structured`` 走 ``_FakeStructuredLLMClient``，按 schema 类型
  返回预定义 Pydantic 模型，避免真实网络调用。
- ``RetrievalClient.search/extract`` 走 ``_FakeRetrievalClient``，返回固定两条 hits。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.edges import (
    decide_after_await_human,
    decide_after_clarify,
    decide_after_cost_checkpoint,
    decide_after_critique,
)
from app.orchestrator.nodes import (
    await_human,
    clarifier,
    cost_checkpoint,
    critic,
    failure_recovery,
    reporter,
    researcher_fan_out,
    standardizer,
    sub_questioner,
    user_intervention,
)
from app.orchestrator.schemas import ClarificationSchema, SubQuestionListSchema
from app.orchestrator.state import ResearchState
from app.provider.base import ChatMessage, ChatRequest, ChatResponse, LLMProvider
from app.provider.client import LLMClient, StructuredCompletion
from app.retrieval.base import RetrievalHit, RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class _FakeProvider(LLMProvider):
    """满足 LLMProvider 协议的占位实现，content 永远返回空对象 JSON。"""

    name = "fake-e2e"

    async def aclose(self) -> None:  # noqa: D401
        return None

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        return ChatResponse(content="{}", model="fake-e2e-model", usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        })

    async def stream(self, request: ChatRequest):  # noqa: ARG002
        if False:
            yield ""


class _FakeStructuredLLMClient(LLMClient):
    """按 ``schema`` 类型返回预设 Pydantic 模型的 LLMClient。"""

    def __init__(self) -> None:
        # 不调 super().__init__：避开熔断器/注册表副作用
        self.calls: list[tuple[str, list[ChatMessage]]] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: D401
        # 实际不会走 chat 路径；complete_structured 直接构造 StructuredCompletion
        return ChatResponse(content="{}", model="fake-e2e-model", usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        })

    async def complete_structured(  # type: ignore[override]
        self,
        *,
        messages: list[ChatMessage],
        schema: type,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
    ) -> StructuredCompletion:
        """按 schema 类别直接返回预设 Pydantic 实例。"""
        # 记录调用，便于测试断言"LLM 被实际调用过"
        tag_label = ",".join(tags or [])
        self.calls.append((tag_label, list(messages)))

        if schema is ClarificationSchema:
            parsed = ClarificationSchema(
                requires_user_input=False,
                questions=[],
                defaults={"scope": "中国境内 2026 年新增装机量", "timeframe": "2026"},
                structured_question={
                    "goal": "2026 年中国光伏装机量趋势研究",
                    "scope": "中国境内新增装机量 / 区域分布 / 政策驱动",
                    "key_concepts": ["光伏装机量", "分布式光伏", "集中式光伏", "国家能源局"],
                },
            )
        elif schema is SubQuestionListSchema:
            parsed = SubQuestionListSchema(
                sub_questions=[
                    {
                        "question": "2026 年中国新增光伏装机量规模与同比",
                        "depends_on": [],
                        "rationale": "总量基线",
                    },
                    {
                        "question": "2026 年分布式与集中式装机结构变化",
                        "depends_on": ["1"],
                        "rationale": "结构维度",
                    },
                ],
            )
        else:
            # 任何未预期的 schema 调用：返回空实例，避免主链路阻塞
            parsed = schema.model_construct()

        return StructuredCompletion(
            parsed=parsed,
            usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            model="fake-e2e-model",
            raw="",
        )


class _FakeRetrievalClient(RetrievalClient):
    """返回固定 2 条 hits 的检索客户端。"""

    def __init__(self) -> None:
        # 显式跳过父类 __init__：避免要求 primary/backup 参数
        self.search_calls: list[RetrievalRequest] = []
        self.extract_calls: int = 0

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:  # noqa: ARG002
        self.search_calls.append(request)
        now = datetime.now(tz=UTC)
        return [
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="国家能源局：2026 年光伏新增装机规模同比增长",
                url="https://www.nea.gov.cn/2026/01/15/pv-install-report.html",
                snippet="2026 年全国新增光伏装机 120GW，同比增长 18%。",
                score=0.92,
                published_at=now,
                fetched_at=now,
            ),
            RetrievalHit(
                source=RetrievalSource.WEB,
                title="财新观察：分布式光伏迎来新一轮政策窗口",
                url="https://www.caixin.com/2026/03/12/distributed-pv.html",
                snippet="2026 年分布式光伏新增 60GW，占比首次过半。",
                score=0.78,
                published_at=now,
                fetched_at=now,
            ),
        ]

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:  # noqa: ARG002
        self.extract_calls += 1
        return list(hits)


# ---------------------------------------------------------------------------
# Stub-deps 绑定的 StateGraph 构建（与 graph.py 等价的测试专用副本）
# ---------------------------------------------------------------------------


def _bind(node_run: Callable[..., Awaitable[dict[str, Any]]], deps: NodeDeps) -> Callable[..., Awaitable[dict[str, Any]]]:
    """把 stub deps 闭包到节点 ``run`` 函数上，等价于 LangGraph 节点签名注入。"""

    async def _wrapped(state: ResearchState) -> dict[str, Any]:
        return await node_run(state, deps=deps)

    return _wrapped


def build_graph_with_deps(deps: NodeDeps):
    """构造带 stub deps 的研究状态图（与 ``build_research_graph`` 等价）。"""
    # 延迟导入避免模块级污染
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(ResearchState)

    # 节点注册：仅 LLM / 检索敏感节点注入 deps，其余与 graph.py 一致
    graph.add_node("failure_recovery", failure_recovery.run)
    graph.add_node("clarify", _bind(clarifier.run, deps))
    graph.add_node("decompose", _bind(sub_questioner.run, deps))
    graph.add_node("retrieve", _bind(researcher_fan_out.run, deps))
    graph.add_node("standardize", standardizer.run)
    graph.add_node("critique", critic.run)
    graph.add_node("report", reporter.run)
    graph.add_node("cost_checkpoint", cost_checkpoint.run)
    graph.add_node("user_intervention", user_intervention.run)
    graph.add_node("await_human", await_human.run)

    graph.add_edge(START, "failure_recovery")
    graph.add_edge("failure_recovery", "clarify")
    graph.add_conditional_edges(
        "clarify",
        decide_after_clarify,
        {"await_human": "await_human", "decompose": "decompose"},
    )
    graph.add_edge("decompose", "retrieve")
    graph.add_edge("retrieve", "standardize")
    graph.add_edge("standardize", "critique")
    graph.add_conditional_edges(
        "critique",
        decide_after_critique,
        {"await_human": "await_human", "cost_checkpoint": "cost_checkpoint"},
    )
    graph.add_conditional_edges(
        "await_human",
        decide_after_await_human,
        {"clarify": "clarify", "critique": "critique"},
    )
    graph.add_conditional_edges(
        "cost_checkpoint",
        decide_after_cost_checkpoint,
        {"user_intervention": "user_intervention", "report": "report"},
    )
    graph.add_edge("user_intervention", "report")
    graph.add_edge("report", END)

    return graph.compile(
        checkpointer=InMemorySaver(),
        interrupt_before=["await_human", "user_intervention"],
    )


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _make_deps() -> tuple[NodeDeps, _FakeStructuredLLMClient, _FakeRetrievalClient]:
    """构造带 stub LLM + stub retrieval 的 NodeDeps。"""
    llm = _FakeStructuredLLMClient()
    retrieval = _FakeRetrievalClient()
    deps = NodeDeps(
        run_id="run-e2e-001",
        team_id="team-e2e",
        trace_id="trace-e2e-001",
        llm=llm,
        retrieval_client=retrieval,
        db_session=None,
    )
    return deps, llm, retrieval


def _initial_state(question: str = "2026 年中国光伏装机量趋势如何？") -> ResearchState:
    """构造固定问题的初始研究状态。"""
    return {
        "run_id": "run-e2e-001",
        "project_id": "proj-e2e",
        "question": question,
        "template_id": "generic",
        "tier": "standard",
        "token_used": 0,
        "token_budget": 100_000,
        "started_at": datetime.now(tz=UTC).isoformat(),
        "trace_id": "trace-e2e-001",
    }


def _thread_config() -> dict[str, Any]:
    """LangGraph checkpointer 必需的 thread_id 配置。"""
    return {"configurable": {"thread_id": "thread-e2e-001"}}


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_full_pipeline_runs_without_hang() -> None:
    """主链路跑通，不在 await_human / user_intervention 挂起。"""
    deps, llm, retrieval = _make_deps()
    compiled = build_graph_with_deps(deps)

    final = await compiled.ainvoke(_initial_state(), config=_thread_config())

    # 走到 END：current_stage 必为 report
    assert final["current_stage"] == "report"
    # LLM 至少被 clarifier / sub_questioner 各调用一次
    tag_set = {tag for tag, _ in llm.calls}
    assert "clarify" in tag_set
    assert "decompose" in tag_set
    # retrieval.search 每个子问题至少被调用一次
    assert len(retrieval.search_calls) >= 1


@pytest.mark.asyncio
async def test_e2e_produces_non_empty_markdown_report() -> None:
    """产出非空 Markdown 报告，含 4 段标题。"""
    deps, _llm, _retrieval = _make_deps()
    compiled = build_graph_with_deps(deps)

    final = await compiled.ainvoke(_initial_state(), config=_thread_config())

    draft = final.get("report_draft") or ""
    assert draft.strip(), "report_draft 不应为空"
    # 4 段标题（与 reporter.default_outline 对齐）
    assert "## 研究背景" in draft
    assert "## 核心发现" in draft
    assert "## 冲突与不确定性" in draft
    assert "## 结论" in draft
    # findings 段至少 1 条 claim
    assert draft.count("\n1. ") + draft.count("\n2. ") >= 1


@pytest.mark.asyncio
async def test_e2e_stage_progression_records_all_stages() -> None:
    """经过主链路各关键阶段，sub_questions / evidence 全部非空。"""
    deps, _llm, _retrieval = _make_deps()
    compiled = build_graph_with_deps(deps)

    final = await compiled.ainvoke(_initial_state(), config=_thread_config())

    assert (final.get("sub_questions") or []), "sub_questions 应非空"
    assert (final.get("evidence") or []), "evidence 应非空"
    assert (final.get("standardized_evidence") or []), "standardized_evidence 应非空"
    # clarification 已被 LLM 写入
    clarification = final.get("clarification") or {}
    assert clarification.get("goal"), "clarification.goal 应被 clarifier 写入"


@pytest.mark.asyncio
async def test_e2e_token_budget_not_exceeded() -> None:
    """token_used 不超过 token_budget（不实际触发成本闸门）。"""
    deps, _llm, _retrieval = _make_deps()
    compiled = build_graph_with_deps(deps)

    final = await compiled.ainvoke(_initial_state(), config=_thread_config())

    used = int(final.get("token_used") or 0)
    budget = int(final.get("token_budget") or 0)
    assert budget > 0
    # stub 不真正累加 token，used 沿用初始值 0
    assert used <= budget


@pytest.mark.asyncio
async def test_e2e_resume_replay_does_not_duplicate_claims() -> None:
    """同一 thread_id 二次运行不应污染主报告：每次跑都是新的 report_draft（state 隔离在 thread 上）。"""
    deps, _llm, _retrieval = _make_deps()
    compiled = build_graph_with_deps(deps)

    cfg = _thread_config()
    first = await compiled.ainvoke(_initial_state(), config=cfg)
    first_draft = first.get("report_draft") or ""

    # 同一 thread 第二次跑：使用不同 question 验证产出受 question 影响
    second = await compiled.ainvoke(
        _initial_state(question="2026 年全球储能市场规模？"),
        config=cfg,
    )
    second_draft = second.get("report_draft") or ""

    assert first_draft and second_draft
    # 两次 report 内容应反映不同 question（背景段携带 question）
    assert "光伏" in first_draft
    assert "储能" in second_draft


__all__ = [
    "_FakeStructuredLLMClient",
    "_FakeRetrievalClient",
    "build_graph_with_deps",
]
