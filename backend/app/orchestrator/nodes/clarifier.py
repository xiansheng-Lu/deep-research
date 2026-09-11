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


# M1 内置最小提示词；M2 起迁入 prompt_registry（§6.5.2 注记）
_SYSTEM_PROMPT_ZH = (
    "你是 AI 研究助手的前置澄清助手。"
    "请判断用户问题是否需要在研究启动前追问："
    "（1）当问题主题、时间范围、地理范围、对比对象或输出风格存在显著歧义时，"
    "应主动生成 1-3 条澄清问题；"
    "（2）否则给出结构化查询（目标 / 范围 / 关键概念 / 约束）。"
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
    if not isinstance(answers, dict):
        return {}
    return {"clarification": {"_human_answers": dict(answers)}}


__all__ = ["run", "clarification_to_state_payload", "_SYSTEM_PROMPT_ZH"]
