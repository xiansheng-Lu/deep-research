"""意图路由 Agent：研究引擎入口分流（PRD 模块 G / 协作规格 §3.1）。

职责边界：
- 只判别"闲聊 / 研究 / 不确定"并给出研究推荐参数；
- 不做检索、不生成报告、不创建 ResearchRun、不落任何项目数据。

保守策略（PRD 模块 G 边界约定）：
- LLM 不可用、调用超时或解析失败时一律降级为 research 并标记 degraded，
  宁可误跑深度研究，不可漏掉真实研究需求；
- 调用方可用 ``force`` 手动绕过自动判别（PRD"强制闲聊/强制研究"）。
"""

from __future__ import annotations

import asyncio
from typing import Literal, cast

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, AgentResult
from app.core.logging import get_logger
from app.orchestrator.schemas import IntentClassification
from app.provider.base import ChatMessage
from app.provider.client import LLMClient
from app.quota.tiers import Tier, tier_budget

log = get_logger("agent.intent_router")

IntentName = Literal["chat", "research", "uncertain"]
ForceIntent = Literal["chat", "research"]

# 判定类任务要求确定性输出，固定温度 0
_CLASSIFY_TEMPERATURE = 0.0
# 判别硬超时：超过则保守降级为研究（PRD 要求判别失败默认走研究）
_CLASSIFY_TIMEOUT_S = 8.0
# 短问题走快速档的字符阈值（粗略启发式，M2 末随评估集标定）
# 中文信息密度高，25 字已足以表达完整研究问题；英文约 4-5 个单词
_QUICK_MAX_CHARS = 25
# 当前唯一模板
_DEFAULT_TEMPLATE = "generic"

_SYSTEM_PROMPT_ZH = (
    "你是深度研究产品的入口意图分流助手，只做一件事：判断用户输入应走"
    "「闲聊直答」还是「深度研究」。\n"
    "三类意图的判定标准：\n"
    "- chat（闲聊直答）：问候寒暄、口语化交流；写作/翻译/改写/摘要等文案加工；"
    "编程、数学、常识等单模型可直接回答、无需多来源核验的问题；无明确外部事实核验需求。\n"
    "- research（深度研究）：包含可检索的实体与主题，需要多来源信息、交叉核验或较新外部事实；"
    "出现对比、趋势、进展、综述、盘点、选型、行业/市场分析等研究意图；"
    "问题有明确对象但需要系统性展开。\n"
    "- uncertain（不确定）：输入过短或语义残缺，无法判断方向；闲聊与研究解释都成立。\n"
    "判别维度：问题长度与具体性、研究关键词（对比/趋势/分析/综述/最新/盘点/选型等）、"
    "是否需要多源验证、是否含可检索实体。\n"
    "保守原则：拿不准时在 research 与 uncertain 之间偏向 research，避免漏掉深度研究需求；"
    "只有明显无需外部信息、单模型直接应答的输入才判 chat。\n"
    "示例：\n"
    "『你好，今天过得怎么样』→ chat；\n"
    "『帮我写一封诚恳的求职邮件』→ chat；\n"
    "『Python 列表怎么去重』→ chat；\n"
    "『对比比亚迪与特斯拉的自动驾驶技术差异』→ research；\n"
    "『2024 至 2026 年向量数据库领域的主要趋势和代表产品』→ research；\n"
    "『盘点国内 RAG 平台的落地案例并给出选型建议』→ research；\n"
    "『新能源』（只有名词、无任何方向）→ uncertain。\n"
    "confidence 给 0-1 的估计值；reason 用一句中文说明关键依据，不超过 50 字。"
    "严格按 JSON 输出，不要包含任何额外文字。"
)


class IntentDecision(BaseModel):
    """意图判别对内决策结果（API 响应模型由 schemas/intent.py 承接）。"""

    intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0)
    # 研究推荐参数：chat 路径不适用，置 None
    recommended_template: str | None = None
    recommended_tier: str | None = None
    estimated_token_budget: int | None = None
    estimated_cost_grade: str | None = None
    # 结果来源：forced 手动强制 / llm 模型判别 / fallback 保守降级
    source: Literal["forced", "llm", "fallback"]
    # 是否降级（LLM 不可用/超时/解析失败时为 True，前端需显式说明）
    degraded: bool = False
    reason: str = ""


def recommend_tier(text: str) -> Tier:
    """按问题长度粗分推荐档位：短问题快速档，其余标准档。"""
    return Tier.QUICK if len(text.strip()) <= _QUICK_MAX_CHARS else Tier.STANDARD


def _research_decision(
    *,
    confidence: float,
    source: Literal["llm", "forced", "fallback"],
    degraded: bool,
    reason: str,
    text: str,
) -> IntentDecision:
    """构造研究路径决策（uncertain 同样按保守策略走研究参数）。"""
    tier = recommend_tier(text)
    return IntentDecision(
        intent="research",
        confidence=confidence,
        recommended_template=_DEFAULT_TEMPLATE,
        recommended_tier=tier.value,
        estimated_token_budget=tier_budget(tier),
        estimated_cost_grade=tier.value,
        source=source,
        degraded=degraded,
        reason=reason,
    )


def _forced_decision(text: str, force: ForceIntent) -> IntentDecision:
    """手动绕过判别（PRD 强制闲聊/强制研究）。"""
    if force == "chat":
        return IntentDecision(
            intent="chat",
            confidence=1.0,
            source="forced",
            reason="用户手动指定闲聊路径",
        )
    return _research_decision(
        confidence=1.0,
        source="forced",
        degraded=False,
        reason="用户手动指定深度研究路径",
        text=text,
    )


async def classify_intent(
    text: str,
    *,
    llm: LLMClient | None,
    force: ForceIntent | None = None,
) -> IntentDecision:
    """判别用户输入意图。

    Args:
        text: 用户原始输入。
        llm: 已注入的 LLM 客户端；None 或调用失败时保守降级为 research。
        force: 手动强制路径，绕过模型判别。
    """
    if force is not None:
        return _forced_decision(text, force)

    if llm is None:
        log.warning("意图判别无可用 LLM，保守降级为研究")
        return _research_decision(
            confidence=0.0,
            source="fallback",
            degraded=True,
            reason="LLM 未配置，按保守策略默认走深度研究",
            text=text,
        )

    try:
        completion = await asyncio.wait_for(
            llm.complete_structured(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM_PROMPT_ZH),
                    ChatMessage(role="user", content=text),
                ],
                schema=IntentClassification,
                temperature=_CLASSIFY_TEMPERATURE,
                max_tokens=300,
                tags=["intent_classify"],
            ),
            timeout=_CLASSIFY_TIMEOUT_S,
        )
    except TimeoutError:
        log.warning("意图判别超时，保守降级为研究", extra={"timeout_s": _CLASSIFY_TIMEOUT_S})
        return _research_decision(
            confidence=0.0,
            source="fallback",
            degraded=True,
            reason="意图判别超时，按保守策略默认走深度研究",
            text=text,
        )
    except Exception as exc:  # noqa: BLE001 - 判别失败统一保守降级
        log.warning("意图判别失败，保守降级为研究", extra={"error": repr(exc)})
        return _research_decision(
            confidence=0.0,
            source="fallback",
            degraded=True,
            reason="意图判别服务暂不可用，按保守策略默认走深度研究",
            text=text,
        )

    result = cast(IntentClassification, completion.parsed)
    # uncertain 按 PRD 保守策略并入研究路径，但保留 intent=uncertain 供前端提示
    if result.intent == "chat":
        return IntentDecision(
            intent="chat",
            confidence=result.confidence,
            source="llm",
            reason=result.reason,
        )
    return _research_decision(
        confidence=result.confidence,
        source="llm",
        degraded=False,
        reason=result.reason,
        text=text,
    ).model_copy(update={"intent": result.intent})


async def run(ctx: AgentContext) -> AgentResult:
    """Agent 契约入口（M0 占位签名保留；M2 起由 API 层直接调用 classify_intent）。"""
    return AgentResult(agent="intent_router")
