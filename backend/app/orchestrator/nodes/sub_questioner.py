"""子问题拆解节点（§6.5.3）。

契约：
- 输入：``ResearchState`` 含 ``clarification`` / ``tier``；可选 ``template_id``。
- 输出：``sub_questions`` 列表写入 state，每项含 ULID ``id`` + ``question`` +
    ``depends_on`` + ``status``；档位限制 quick≤3 / standard≤5 / deep≤8 / extreme≤12。
- 行为：
    - LLM 可用时调用 ``deps.llm.complete_structured(schema=SubQuestionListSchema)``，
      按依赖关系拓扑分配子问题 ID 并写入 state。
    - LLM 不可用或调用失败时走降级：以澄清后的 ``goal`` 为唯一子问题，确保流程可继续。
    - 任何越界子问题数量都会被截断到档位上限（LLM 偶发多产出时不阻塞流程）。

占位签名（兼容 M0）：无 deps 时函数仍能运行。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.persistence import persist_sub_questions
from app.orchestrator.schemas import SubQuestionItem, SubQuestionListSchema
from app.orchestrator.state import ResearchStage, ResearchState, SubQuestionDict, SubQuestionStatus
from app.provider.base import ChatMessage
from app.quota.tiers import Tier

log = get_logger("orchestrator.sub_questioner")


# 档位 → 最大子问题数（§6.5.3 约束）
_TIER_MAX_SUB_QUESTIONS: dict[Tier, int] = {
    Tier.QUICK: 3,
    Tier.STANDARD: 5,
    Tier.DEEP: 8,
    Tier.EXTREME: 12,
}


def max_sub_questions_for(tier: str | None) -> int:
    """按档位返回允许的最大子问题数；未知档位按 ``standard`` 处理。"""
    try:
        parsed = Tier(tier) if tier else Tier.STANDARD
    except ValueError:
        parsed = Tier.STANDARD
    return _TIER_MAX_SUB_QUESTIONS[parsed]


_SYSTEM_PROMPT_ZH = (
    "你是 AI 研究助手的研究规划助手。"
    "请把用户的研究目标拆解为 3-12 条独立的子问题，按依赖关系排序；"
    "每条子问题必须是可独立检索的，不与其它子问题重叠。"
    "depends_on 只能填写前置子问题的数字序号字符串（例如 “1”、“2”），"
    "序号从 1 开始计数，且只能引用排在当前子问题之前的条目；无依赖时填空数组 []。"
    "严格按 JSON 输出，不要包含任何额外文字。"
)


async def _call_llm(
    *,
    deps: NodeDeps | None,
    payload: dict[str, Any],
    max_tokens: int = 1500,
) -> tuple[SubQuestionListSchema | None, int]:
    """调用 LLM 完成结构化拆解。

    返回 ``(结构化结果, 本次调用消耗总 token)``；失败/不可用返回 ``(None, 0)``。
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
                ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            schema=SubQuestionListSchema,
            max_tokens=max_tokens,
            tags=["decompose"],
        )
    except Exception as exc:  # noqa: BLE001 - 统一收敛
        log.warning(
            "sub_questioner LLM 调用失败，降级处理",
            extra={"run_id": getattr(deps, "run_id", None), "error": repr(exc)},
        )
        return None, 0
    if not isinstance(completion.parsed, SubQuestionListSchema):
        return None, 0
    return completion.parsed, int(completion.usage.get("total_tokens", 0))


def _assign_ids(items: list[SubQuestionItem], max_count: int) -> list[SubQuestionDict]:
    """为子问题分配 ULID 主键 + 拓扑解析依赖（把 depends_on 中的序号映射为 ID）。

    实现要点：
    - 截断到 ``max_count`` 条；超过部分丢弃（保留前 N 条）。
    - depends_on 使用序号（1-based）表达；映射到 ULID 后再落库，便于后续节点引用。
    """
    truncated = items[:max_count]
    id_map: dict[int, str] = {}
    for idx, _item in enumerate(truncated, start=1):
        id_map[idx] = new_ulid()

    result: list[SubQuestionDict] = []
    for idx, item in enumerate(truncated, start=1):
        new_id = id_map[idx]
        resolved_deps: list[str] = []
        for raw in item.depends_on or []:
            try:
                dep_idx = int(raw)
            except (TypeError, ValueError):
                # 允许直接传 ID（防御性）
                if isinstance(raw, str) and raw in id_map.values():
                    resolved_deps.append(raw)
                continue
            if 1 <= dep_idx <= idx - 1 and dep_idx in id_map:
                resolved_deps.append(id_map[dep_idx])
        result.append(
            {
                "id": new_id,
                "question": item.question,
                "depends_on": resolved_deps,
                "status": "pending",
                "evidence_ids": [],
            }
        )
    return result


def _fallback(clarification: dict[str, Any] | None, original_question: str | None) -> list[SubQuestionDict]:
    """降级路径：以原问题或澄清目标生成 1 条子问题。"""
    goal = ""
    if isinstance(clarification, dict):
        goal = str(clarification.get("goal") or "")
    if not goal:
        goal = (original_question or "").strip()
    if not goal:
        goal = "请研究该主题。"
    return [
        {
            "id": new_ulid(),
            "question": goal,
            "depends_on": [],
            "status": "pending",
            "evidence_ids": [],
        }
    ]


async def _maybe_persist(
    deps: NodeDeps | None,
    state: ResearchState,
    items: list[SubQuestionDict],
) -> None:
    """注入了 DB 会话时把新拆解的子问题落库（幂等，无会话则静默跳过）。"""
    if deps is None or deps.db_session is None or not items:
        return
    await persist_sub_questions(
        deps.db_session,
        run_id=str(state.get("run_id") or ""),
        items=items,
    )


@instrument(ResearchStage.DECOMPOSE)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """子问题拆解节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中。
    """
    # 已拆解则直接跳过（幂等）
    existing = state.get("sub_questions") or []
    if existing:
        return {"sub_questions": list(existing)}

    tier = state.get("tier") or "standard"
    max_count = max_sub_questions_for(tier)
    clarification = state.get("clarification") or {}

    payload = {
        "question": clarification,
        "original_question": state.get("question"),
        "template_id": state.get("template_id"),
        "tier": tier,
        "max_sub_questions": max_count,
    }

    # 1) LLM 命中
    parsed, consumed_tokens = await _call_llm(deps=deps, payload=payload)
    # 真实调用产生的 token 用量累加回写 state（成本闸门依据）
    token_patch = {"token_used": int(state.get("token_used") or 0) + consumed_tokens}
    if parsed is not None:
        items = list(parsed.sub_questions)
        if items:
            result_sqs = _assign_ids(items, max_count=max_count)
            await _maybe_persist(deps, state, result_sqs)
            return {
                "sub_questions": result_sqs,
                **token_patch,
            }

    # 2) 降级：单子问题直通
    fallback_sqs = _fallback(clarification, state.get("question"))
    await _maybe_persist(deps, state, fallback_sqs)
    return {
        "sub_questions": fallback_sqs,
    }


__all__ = ["run", "max_sub_questions_for", "SubQuestionStatus"]
