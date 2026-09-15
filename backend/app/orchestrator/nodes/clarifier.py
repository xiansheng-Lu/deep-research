"""澄清节点：判定查询是否需要用户追问（§6.5.2）。

契约：
- 输入：``ResearchState`` 至少含 ``question``；可选 ``clarification``（已澄清则跳过）。
- 输出：合并到 state 的字段包括
    ``needs_clarification`` / ``interrupt_reason`` / ``interrupt_payload`` /
    ``clarification`` / ``current_stage`` / ``updated_at``。
- 行为：
    - 已澄清（``state["clarification"]`` 存在）→ 直接跳过。
    - LLM 可用时调用 ``deps.llm.complete_structured(schema=ClarificationSchema)``；
      判定需追问则设置 ``needs_clarification=True`` + ``interrupt_reason="clarify"`` +
      ``interrupt_payload``，由状态图（§6.3）路由到 ``await_human``。
    - LLM 不可用或调用失败时走降级：视查询为"无需追问"，直接生成最小可用 ``clarification``。

占位签名（兼容 M0）：无 deps 时函数仍能运行；图骨架在 WP-6 接入 NodeDeps 后会切换为
``async def run(state, *, deps)`` 的统一形态。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.schemas import ClarificationSchema
from app.orchestrator.state import ResearchStage, ResearchState
from app.provider.base import ChatMessage

log = get_logger("orchestrator.clarifier")


# 澄清判定温度：判定类任务要求确定性，固定 0 避免同问不同判（M1 口径；
# 意图路由的闲聊/研究阈值标定属 M2-1，不在此处）
_CLARIFY_TEMPERATURE = 0.0

# M1 内置提示词；M2 起迁入 prompt_registry（§6.5.2 注记）
# 口径要点（2026-09-12 联调校准）：默认放行、仅硬歧义才追问。
# 旧口径"任一维度存在歧义即追问"被模型宽泛解释为"可以更细就追问"，
# 导致主题明确的开放研究问题（对比/趋势/进展类）在 M1 无澄清恢复入口
# 的情况下全部挂起，端到端链路无法走通。
_SYSTEM_PROMPT_ZH = (
    "你是 AI 研究助手的前置澄清助手。你的职责是判断研究是否可以立即启动，"
    "而不是把问题打磨到最完美。判定原则："
    "1. 默认放行：只要问题包含可识别的研究主题，就判定无需追问；"
    "时间、地域、对比维度、输出形式、篇幅、侧重点等未写明的细节，"
    "一律由你在 structured_question 中给出合理默认，不得因此追问。"
    "2. 仅在以下硬性情形才追问：(a) 问题缺少研究主题或主题无法识别；"
    "(b) 问题过短或过于宽泛、无法确定任何研究方向（如单个宽泛名词）；"
    "(c) 问题自相矛盾或关键限定冲突，不澄清会导致研究方向错误。"
    "3. 追问最多 2 条，每条必须给出可选项与推荐项；"
    "禁止追问输出风格、报告篇幅、维度侧重等可自行默认的内容。"
    "示例：『对比 PostgreSQL 与 MongoDB 在 JSON 文档查询场景下的索引机制差异』"
    "无需追问；『2024 至 2026 年 AI 编程助手领域的主要技术趋势和代表性产品』"
    "无需追问；『人工智能』这种无法确定方向的单个宽泛名词才需要追问。"
    "无需追问时 requires_user_input=false 并补全 structured_question；"
    "需要追问时 requires_user_input=true 且 questions 给 1-2 条带选项的问题。"
    "严格按 JSON 输出，不要包含任何额外文字。"
)


def _needs_clarification(question: str) -> bool:
    """极简启发式：含明显问号且长度 < 8 的查询视为"需要追问"。

    仅供降级路径使用——LLM 可用时永远优先走 LLM 判定。
    """
    stripped = (question or "").strip()
    if not stripped:
        return True
    return len(stripped) < 8


async def _call_llm(
    *,
    deps: NodeDeps | None,
    question: str,
    max_tokens: int = 800,
) -> tuple[ClarificationSchema | None, int]:
    """调用 LLM 完成结构化判定。

    返回 ``(结构化结果, 本次调用消耗总 token)``；LLM 不可用或调用失败时
    返回 ``(None, 0)``，由调用方走降级路径。
    """
    if deps is None:
        return None, 0
    llm = getattr(deps, "llm", None)
    if llm is None:
        return None, 0
    try:
        completion = await llm.complete_structured(
            messages=[
                ChatMessage(role="system", content=_SYSTEM_PROMPT_ZH),
                ChatMessage(role="user", content=question),
            ],
            schema=ClarificationSchema,
            temperature=_CLARIFY_TEMPERATURE,
            max_tokens=max_tokens,
            tags=["clarify"],
        )
    except Exception as exc:  # noqa: BLE001 - 统一收敛
        log.warning(
            "clarifier LLM 调用失败，降级处理",
            extra={"run_id": getattr(deps, "run_id", None), "error": repr(exc)},
        )
        return None, 0
    if not isinstance(completion.parsed, ClarificationSchema):
        log.warning(
            "clarifier LLM 返回非预期类型",
            extra={"got": type(completion.parsed).__name__},
        )
        return None, 0
    return completion.parsed, int(completion.usage.get("total_tokens", 0))


def _build_interrupt_payload(result: ClarificationSchema) -> dict[str, Any]:
    """按 §6.5.2 约定把 ClarificationSchema 序列化为前端可直接渲染的载荷。"""
    return {
        "questions": [q.model_dump() for q in result.questions],
        "defaults": dict(result.defaults),
    }


@instrument(ResearchStage.CLARIFY)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """澄清节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中，键名严格匹配 §6.2 / §6.3。
    """
    # 1) 已澄清 → 跳过
    if state.get("clarification"):
        return {"needs_clarification": False}

    question = (state.get("question") or "").strip()
    if not question:
        return {
            "needs_clarification": True,
            "interrupt_reason": "clarify",
            "interrupt_payload": {
                "questions": [
                    {
                        "key": "topic",
                        "text": "请提供你想研究的核心问题。",
                        "options": [],
                        "recommended": None,
                    }
                ],
                "defaults": {},
            },
        }

    # 2) 走 LLM 判定
    result, consumed_tokens = await _call_llm(deps=deps, question=question)

    # 真实调用产生的 token 用量累加回写 state（成本闸门依据）
    token_patch = {"token_used": int(state.get("token_used") or 0) + consumed_tokens}

    # 3) 降级路径
    if result is None:
        if _needs_clarification(question):
            return {
                "needs_clarification": True,
                "interrupt_reason": "clarify",
                "interrupt_payload": {
                    "questions": [
                        {
                            "key": "scope",
                            "text": "请补充问题的目标、范围或关键背景，以便我们更精准地检索。",
                            "options": [],
                            "recommended": None,
                        }
                    ],
                    "defaults": {},
                },
            }
        clarification = {
            "goal": question,
            "scope": "",
            "key_concepts": [],
            "constraints": [],
            "_source": "clarifier.fallback",
        }
        return {"clarification": clarification, "needs_clarification": False}

    # 4) LLM 判定需要追问
    if result.requires_user_input:
        interrupt_payload = _build_interrupt_payload(result)
        return {
            "needs_clarification": True,
            "interrupt_reason": "clarify",
            "interrupt_payload": interrupt_payload,
            **token_patch,
        }

    # 5) LLM 判定无需追问 → 落 structured_question + defaults
    structured = dict(result.structured_question)
    defaults = dict(result.defaults)
    clarification = {**defaults, **structured, "_source": "clarifier.llm"}
    return {
        "clarification": clarification,
        "needs_clarification": False,
        **token_patch,
    }


# ---------------------------------------------------------------------------
# 内部工具：供测试与外部复用
# ---------------------------------------------------------------------------


def clarification_to_state_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """将用户在 ``await_human`` 后提交的答案合并入 ``state.clarification``。

    期望 ``payload`` 形如 ``{"answers": {"scope": "...", "timeframe": "..."}}``；
    与已有 ``state["clarification"]`` 浅合并后返回。
    """
    if not payload:
        return {}
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict) or not answers:
        return {}
    return {"clarification": {"_human_answers": dict(answers)}}


__all__ = ["run", "clarification_to_state_payload", "_SYSTEM_PROMPT_ZH"]
