"""Reporter 结构化终稿的中文 prompt 构造（M2-7）。

材料按《智能体协作规格说明》§6 压缩为「摘要+引用」：不向 LLM 传证据正文，
只给证据摘要池与收敛论断；模型只能引用池内 evidence_id，分歧块由后端注入。
"""

from __future__ import annotations

import json

from app.orchestrator.state import EvidenceDict, ReportClaim
from app.provider.base import ChatMessage

REPORT_SYSTEM_PROMPT_ZH = (
    "你是深度研究系统的报告生成 Agent。你的任务是基于给定的收敛论断与证据摘要池，"
    "产出一份中文结构化研究报告的区块计划。\n"
    "硬性规则：\n"
    "1. 只可引用输入证据池中给出的 evidence_id，严禁编造任何证据 id、URL、机构名或数据；\n"
    "2. 结论、事实陈述、数字、百分比、金额、日期、排名等数据点必须在 evidence_ids 中给出"
    "支撑证据；没有来源支撑的综合性判断只能输出为 conclusion 且 confidence=inferred，"
    "且不得包含任何具体数字、日期或排名；\n"
    "3. evidence 类型用于展开关键证据所陈述的事实，limitation 类型用于说明研究范围、"
    "样本、时效与未获复核等局限；\n"
    "4. 不要输出 dispute（分歧）区块，也不要替争议下最终结论，分歧由系统另行呈现；\n"
    "5. 每个区块 text 为 1~3 句中文，客观陈述，不使用 Markdown、不使用角标编号；\n"
    "6. quotes 如需给出，必须是对应证据中真实出现过的原句（用于原文定位），不得改写。"
)


def _evidence_summary(evidence: EvidenceDict) -> dict[str, object]:
    return {
        "id": evidence.get("id"),
        "domain": evidence.get("domain"),
        "title": evidence.get("title"),
        "snippet": evidence.get("snippet"),
        "credibility": evidence.get("credibility"),
        "published_at": evidence.get("published_at"),
    }


def build_report_user_message(
    *,
    question: str,
    goal: str,
    scope: str,
    material: list[EvidenceDict],
    claims: list[ReportClaim],
    signals: dict[str, object],
) -> str:
    """组装首份 blocks 计划的 user 消息（JSON 序列化材料）。"""
    payload = {
        "question": question,
        "goal": goal,
        "scope": scope,
        "evidence_pool": [_evidence_summary(ev) for ev in material],
        "claims": [
            {
                "text": (c.get("text") or "").strip(),
                "confidence": c.get("confidence") or "single_source",
                "evidence_ids": [
                    cit.get("evidence_id")
                    for cit in (c.get("citations") or [])
                    if isinstance(cit, dict) and cit.get("evidence_id")
                ],
            }
            for c in claims
            if (c.get("text") or "").strip()
        ],
        "limitation_signals": signals,
    }
    return "请基于以下材料生成结构化报告区块计划，按系统约定的 JSON Schema 输出：\n" + json.dumps(
        payload, ensure_ascii=False
    )


def build_repair_user_message(base_message: str, violation_texts: list[str]) -> str:
    """自修复轮 user 消息：回传含数字断言但无引用的块文本，要求补证或删除数字。"""
    bullet = "\n".join(f"- {text}" for text in violation_texts)
    feedback = (
        "\n\n上一版计划中以下 conclusion 区块包含数字或事实数据点，但没有提供任何"
        " evidence_ids 支撑，这是不允许的：\n"
        f"{bullet}\n"
        "请重新输出完整的区块计划：为这些论断补充证据池内真实存在的 evidence_ids，"
        "无法补证的，删除其中的具体数字/日期/排名后改为不带数据点的谨慎表述。"
        "其余区块保持可用。"
    )
    return base_message + feedback


def build_messages(
    *,
    question: str,
    goal: str,
    scope: str,
    material: list[EvidenceDict],
    claims: list[ReportClaim],
    signals: dict[str, object],
) -> list[ChatMessage]:
    """首份计划的 system/user 消息对。"""
    return [
        ChatMessage(role="system", content=REPORT_SYSTEM_PROMPT_ZH),
        ChatMessage(
            role="user",
            content=build_report_user_message(
                question=question,
                goal=goal,
                scope=scope,
                material=material,
                claims=claims,
                signals=signals,
            ),
        ),
    ]


__all__ = [
    "REPORT_SYSTEM_PROMPT_ZH",
    "build_messages",
    "build_repair_user_message",
    "build_report_user_message",
]
