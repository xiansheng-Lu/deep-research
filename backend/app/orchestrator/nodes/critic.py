"""审视节点（§6.5.6 critic）。

契约：
- 输入：``ResearchState`` 含 ``standardized_evidence``（WP-5.5 产出），
    可选已有 ``report_claims`` / ``conflicts`` / ``verdicts``（用于回流）。
- 输出：合并到 state 的字段包括 ``report_claims`` / ``conflicts`` /
    ``verdicts`` / ``interrupt_reason`` / ``interrupt_payload`` /
    ``critic_degraded`` / ``current_stage`` / ``updated_at``。
- 行为：
    1. ``generate_draft_claims`` —— M1 简化版：每条 evidence 派生一条
       ``ReportClaim``（confidence=single_source + 1 条引用）。
    2. ``detect_conflicts_semantic`` —— M2-2：词面轻量预筛候选对后，经
       LLM ``complete_structured`` 单批做语义判定（议题/四类型/三严重度/
       一句中文依据，温度 0、8 秒硬超时）；LLM 未配置/异常/超时/脏返回时
       回退 ``detect_conflicts`` Jaccard 启发式并标记 ``critic_degraded``。
    3. ``is_resolvable`` —— severity in {"low", "medium"} → 可自动解决；
       "high" → 需用户裁决。
    4. ``resolve_by_authority`` —— 选 credibility 高的对应 claim；
       平局时按 report_claims 顺序保留首条。
    5. 循环至多 ``MAX_CRITIC_ITERATIONS=3`` 轮；
       三类收敛信号（任一触发即停）：无新冲突 / token_used 超过
       budget×CRITIC_BUDGET_RATIO=0.30 / 迭代满。
    6. 每条新冲突即时落库（``persist_conflicts``）：low/medium 自动收敛后
       写 resolved（resolved_at，无 Verdict 行，不挂起）；high 写
       awaiting_human，逐条推送 ``conflict.detected`` WS 帧并挂起。
    7. 回流时按已落库 Verdict 的 choice 四值收敛（FR-11）：evidence_a/
       evidence_b 取一弃一、both 双方保留并打并存标记、reject 双弃；
       被舍弃 claim 不进入结论段。全部 high 冲突有对应 verdict 后不再挂起。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.persistence import persist_conflicts
from app.orchestrator.schemas import ConflictDetectionSchema
from app.orchestrator.state import (
    ConflictDict,
    EvidenceDict,
    ReportClaim,
    ResearchStage,
    ResearchState,
    VerdictDict,
)
from app.provider.base import ChatMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.provider.client import LLMClient
    from app.realtime.hub import RealtimeHub

log = get_logger("orchestrator.critic")

# ---------------------------------------------------------------------------
# 常量（§6.5.6）
# ---------------------------------------------------------------------------

MAX_CRITIC_ITERATIONS: int = 3
"""审视节点最多迭代轮数。"""

CRITIC_BUDGET_RATIO: float = 0.30
"""审视阶段消耗的 budget 比例上限（占档位 token 预算 30%）。"""

CONFLICT_SIMILARITY_THRESHOLD: float = 0.4
"""两两 evidence 的 Jaccard 词集合相似度阈值；高于该值才视为同一议题（启发式口径）。"""

# ---------------------------------------------------------------------------
# M2-2 LLM 语义检测常量（§6.5.6 / FR-1 / NFR-1/3）
# ---------------------------------------------------------------------------

LLM_BATCH_TIMEOUT_SECONDS: float = 8.0
"""单批语义判定硬超时（秒）；超时即降级，不拖垮研究主链路。"""

LLM_DETECTION_TEMPERATURE: float = 0.0
"""检测判定温度固定 0（NFR-3 确定性）。"""

LLM_DETECTION_MAX_TOKENS: int = 1200
"""单批输出上限：12 个候选对 × 每对一句依据的量级。"""

MAX_LLM_CANDIDATE_PAIRS: int = 12
"""进入 LLM 判定的候选对上限：控制 O(n²) token 成本（12 证据全两两为 66 对）。"""

LLM_CANDIDATE_SIMILARITY_THRESHOLD: float = 0.12
"""词面预筛阈值（召回取向，显著松于启发式裁决阈值 0.4）。

同一议题、结论相反的证据通常共享主题词（专名/指标名），词面重叠低但不会为零；
预筛只负责「同议题」粗召回，「是否真冲突」交给 LLM 语义判定，
因此不再要求双方可信度不同（temporal/perspective 冲突可发生在同可信度之间）。
"""

_CONFLICT_TYPE_DEFAULT: str = "factual"
_CONFLICT_SEVERITY_DEFAULT: str = "medium"

_CREDIBILITY_RANK: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}

# ---------------------------------------------------------------------------
# 文本 → 词集合（M1 简化版：去标点 + 小写 + 长度≥2 的 token）
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def _tokenize(text: str | None) -> set[str]:
    """把文本切成词集合：英文 / 数字按连续段拆，CJK 按单字拆。

    全量保留（含单字），让 Jaccard 召回主要靠 CJK 单字重叠；
    噪声由相似度阈值天然过滤。
    """
    if not text:
        return set()
    tokens: set[str] = set()
    for m in _TOKEN_RE.findall(text.lower()):
        # CJK / 短串按单字拆；英文连续段也按字拆，便于 Jaccard 召回
        for ch in m:
            tokens.add(ch)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度：|A∩B| / |A∪B|；空集约定返回 0.0。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# 草稿论断（M1 简化版）
# ---------------------------------------------------------------------------


def generate_draft_claims(material: list[EvidenceDict]) -> list[ReportClaim]:
    """每条 evidence 派生一条 ReportClaim。

    M1 简化策略：
    - 1 条 evidence → 1 条 claim；
    - confidence 固定 ``single_source``（无 cross_verified 语义证据前不夸口）；
    - citations 仅挂 1 条引用（evidence_id / url / title / snippet）。
    """
    claims: list[ReportClaim] = []
    for ev in material:
        claim_text = ev.get("title") or ev.get("snippet") or ""
        citations: list[dict[str, Any]] = [
            {
                "evidence_id": ev["id"],
                "url": ev["url"],
                "title": ev["title"],
                "snippet": ev["snippet"],
            }
        ]
        claims.append(
            {
                "id": new_ulid(),
                "text": claim_text,
                "confidence": "single_source",
                "citations": citations,
            }
        )
    return claims


# ---------------------------------------------------------------------------
# 冲突检测（M1 启发式）
# ---------------------------------------------------------------------------


def detect_conflicts(
    claims: list[ReportClaim],
    material: list[EvidenceDict],
) -> list[ConflictDict]:
    """M1 冲突检测：两两 evidence 计算 Jaccard 相似度 + 可信度不同。

    冲突构造规则：
    - 相似度 >= ``CONFLICT_SIMILARITY_THRESHOLD`` 视为同一议题；
    - 两 evidence credibility 不同 → 视为潜在冲突；
    - type/severity 采用默认值（M1 暂不细分）。
    """
    if len(material) < 2:
        return []

    # 预计算每条 evidence 的词集合
    tokens_per_ev: list[set[str]] = []
    for ev in material:
        bag = _tokenize(ev.get("title")) | _tokenize(ev.get("snippet"))
        tokens_per_ev.append(bag)

    conflicts: list[ConflictDict] = []
    n = len(material)
    for i in range(n):
        for j in range(i + 1, n):
            ev_i = material[i]
            ev_j = material[j]
            sim = _jaccard(tokens_per_ev[i], tokens_per_ev[j])
            if sim < CONFLICT_SIMILARITY_THRESHOLD:
                continue
            cred_i = ev_i.get("credibility") or "D"
            cred_j = ev_j.get("credibility") or "D"
            if cred_i == cred_j:
                continue
            # 生成冲突
            claim_text = ev_i.get("title") or ev_j.get("title") or ""
            conflicts.append(
                serialize_conflict(
                    {
                        "claim": claim_text,
                        "evidence_a_id": ev_i["id"],
                        "evidence_b_id": ev_j["id"],
                        "type": _CONFLICT_TYPE_DEFAULT,
                        "severity": _CONFLICT_SEVERITY_DEFAULT,
                    }
                )
            )

    return conflicts


def serialize_conflict(payload: dict[str, Any]) -> ConflictDict:
    """把启发式产出的 raw 冲突字典规整为 ConflictDict。

    ``payload`` 期望字段：claim / evidence_a_id / evidence_b_id / type / severity。
    """
    return {
        "id": new_ulid(),
        "claim": payload.get("claim") or "",
        "evidence_a_id": payload["evidence_a_id"],
        "evidence_b_id": payload["evidence_b_id"],
        "type": payload.get("type") or _CONFLICT_TYPE_DEFAULT,
        "severity": payload.get("severity") or _CONFLICT_SEVERITY_DEFAULT,
        "status": "detected",
    }


# ---------------------------------------------------------------------------
# LLM 语义冲突检测（M2-2 §6.5.6 / FR-1 / FR-2）
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT_ZH = (
    "你是深度研究报告的批判审核员。任务：对同一研究议题下两两成对的证据做语义级冲突判定。"
    "冲突类型四值："
    "factual=事实性冲突，双方对同一事实给出互相排斥的数字、事件或结论；"
    "methodological=口径/方法冲突，统计口径、测量方法或样本不同导致结论不可直接比较；"
    "temporal=时间错配，双方数据对应不同时点却被用于同一论断；"
    "perspective=观点分歧，对同一事实给出相反评价或解读。"
    "严重度三值："
    "high=结论直接对立或关键数字矛盾，采信任一方都会改变研究结论，必须由人裁决；"
    "medium=口径或侧重点差异，可按来源可信度与权威性自动消解；"
    "low=措辞、详略或轻微出入，不影响结论。"
    "判定原则：1. 仅当双方针对同一议题且含义实质互斥时才判 is_conflict=true；"
    "互补信息、不同侧面、不同指标一律不判冲突；2. 优先依据语义而非字面措辞，"
    "不要因表述不同就误判；3. claim 用一句中性中文概括争议议题（不带观点、不超过 50 字），"
    "reason 用一句中文给出依据（不超过 50 字）。"
    "严格只输出一个 JSON 对象，结构为 "
    '{"results":[{"pair_index":0,"is_conflict":true,"claim":"...","type":"factual",'
    '"severity":"high","reason":"..."}]}；未判为冲突的配对可省略，'
    "禁止输出 JSON 以外的任何文字。"
    "示例一：A『国家统计局：2026 年光伏新增装机 210GW，同比增长 45%』"
    "对 B『行业协会报告：按并网口径 2026 年光伏实际新增仅 120GW，210GW 含大量未并网立项』"
    "→ is_conflict=true，type=methodological，severity=high，"
    "claim=『2026 年光伏新增装机规模』，reason=『双方数字矛盾且统计口径不同，直接影响结论』。"
    "示例二：A『2026 年光伏新增装机增长』对 B『2026 年光伏组件出口额增长』"
    "→ is_conflict=false，二者指标不同，不构成同一议题冲突。"
)


@dataclass(slots=True)
class DetectionRound:
    """单轮检测结果。

    Attributes:
        conflicts: 本轮检出的冲突。
        degraded: 是否走了启发式降级（LLM 不可用/异常/超时/脏返回）。
        tokens_used: 本轮 LLM 消耗 token（降级路径为 0）。
    """

    conflicts: list[ConflictDict]
    degraded: bool
    tokens_used: int


def build_candidate_pairs(
    material: list[EvidenceDict],
    *,
    threshold: float = LLM_CANDIDATE_SIMILARITY_THRESHOLD,
    cap: int = MAX_LLM_CANDIDATE_PAIRS,
) -> list[tuple[int, int]]:
    """词面轻量预筛：返回送 LLM 判定的候选证据对下标列表。

    - 两两计算 title+snippet 的 Jaccard，``>= threshold`` 视为疑似同议题；
    - 按相似度降序（平局按下标升序）排序后截断到 ``cap`` 对，控制 token 成本；
    - 不要求可信度不同：语义冲突（如 temporal/perspective）可能发生在同可信度间。
    """
    n = len(material)
    if n < 2:
        return []
    bags = [_tokenize(ev.get("title")) | _tokenize(ev.get("snippet")) for ev in material]
    scored: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = _jaccard(bags[i], bags[j])
            if sim >= threshold:
                scored.append((sim, i, j))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(i, j) for _, i, j in scored[:cap]]


def _build_detection_user_message(material: list[EvidenceDict], pairs: list[tuple[int, int]]) -> str:
    """把候选对序列化为中文判定批次（JSON，字段名保持英文以贴齐输出契约）。"""
    payload = {
        "pairs": [
            {
                "pair_index": idx,
                "evidence_a": {
                    "title": material[i].get("title") or "",
                    "snippet": material[i].get("snippet") or "",
                    "domain": material[i].get("domain") or "",
                    "source_level": material[i].get("source_level") or "",
                    "credibility": material[i].get("credibility") or "",
                    "published_at": material[i].get("published_at"),
                },
                "evidence_b": {
                    "title": material[j].get("title") or "",
                    "snippet": material[j].get("snippet") or "",
                    "domain": material[j].get("domain") or "",
                    "source_level": material[j].get("source_level") or "",
                    "credibility": material[j].get("credibility") or "",
                    "published_at": material[j].get("published_at"),
                },
            }
            for idx, (i, j) in enumerate(pairs)
        ]
    }
    return "请逐对判定以下证据是否构成实质冲突，并按系统约定输出 JSON：\n" + json.dumps(
        payload, ensure_ascii=False
    )


def _heuristic_round(material: list[EvidenceDict]) -> DetectionRound:
    """降级轮：回退 M1 Jaccard 启发式并标记 degraded。"""
    return DetectionRound(conflicts=detect_conflicts([], material), degraded=True, tokens_used=0)


def _conflict_from_llm_result(
    result: Any,
    *,
    material: list[EvidenceDict],
    pair: tuple[int, int],
) -> ConflictDict:
    """把单条 LLM 判定映射为 ConflictDict；议题缺省时回退证据标题。"""
    i, j = pair
    ev_i = material[i]
    ev_j = material[j]
    fallback_claim = ev_i.get("title") or ev_j.get("title") or ""
    return serialize_conflict(
        {
            "claim": (getattr(result, "claim", "") or "").strip() or fallback_claim,
            "evidence_a_id": ev_i["id"],
            "evidence_b_id": ev_j["id"],
            "type": getattr(result, "type", _CONFLICT_TYPE_DEFAULT),
            "severity": getattr(result, "severity", _CONFLICT_SEVERITY_DEFAULT),
        }
    )


async def detect_conflicts_semantic(
    material: list[EvidenceDict],
    *,
    llm: LLMClient,
    run_id: str = "",
    exclude_pairs: set[frozenset[str]] | None = None,
) -> DetectionRound:
    """单批 LLM 语义检测；任何不可用情形都回退启发式（FR-2，绝不抛穿）。

    故障口径：未配置由节点层判断（不进本函数）；调用异常（含 Provider 熔断/网络）、
    ``asyncio.wait_for`` 8 秒超时、结构化返回脏数据（越界/重复 pair_index）三类
    均回退 ``detect_conflicts`` 启发式并置 ``degraded=True``。

    ``exclude_pairs`` 为已处理证据 ID 对集合（迭代/回流重入去重）；全部候选都被
    排除时直接返回空结果且不调用 LLM，避免重复计费。
    """
    if len(material) < 2:
        return DetectionRound([], False, 0)

    pairs = build_candidate_pairs(material)
    if exclude_pairs:
        pairs = [
            (i, j) for i, j in pairs if frozenset({material[i]["id"], material[j]["id"]}) not in exclude_pairs
        ]
    if not pairs:
        # 词面预筛无同议题候选（或候选均已处理）：零冲突、零消耗（非降级）
        return DetectionRound([], False, 0)

    try:
        completion = await asyncio.wait_for(
            llm.complete_structured(
                messages=[
                    ChatMessage(role="system", content=_LLM_SYSTEM_PROMPT_ZH),
                    ChatMessage(
                        role="user",
                        content=_build_detection_user_message(material, pairs),
                    ),
                ],
                schema=ConflictDetectionSchema,
                temperature=LLM_DETECTION_TEMPERATURE,
                max_tokens=LLM_DETECTION_MAX_TOKENS,
                tags=["critique"],
            ),
            timeout=LLM_BATCH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log.warning(
            "critic LLM 语义检测单批超过 8s 硬超时，降级启发式检测",
            extra={"run_id": run_id, "candidate_pairs": len(pairs)},
        )
        return _heuristic_round(material)
    except Exception as exc:  # noqa: BLE001 - 未配置/熔断/网络/脏 JSON 解析统一降级
        log.warning(
            "critic LLM 语义检测调用失败，降级启发式检测",
            extra={"run_id": run_id, "error": repr(exc)},
        )
        return _heuristic_round(material)

    parsed = completion.parsed
    if not isinstance(parsed, ConflictDetectionSchema):
        log.warning(
            "critic LLM 返回非预期类型，降级启发式检测",
            extra={"run_id": run_id, "got": type(parsed).__name__},
        )
        return _heuristic_round(material)

    # 脏返回校验：下标越界或重复即视为不可解析，整轮换启发式（保守不采信）
    valid_indices: set[int] = set()
    for item in parsed.results:
        if item.pair_index >= len(pairs) or item.pair_index in valid_indices:
            log.warning(
                "critic LLM 返回非法 pair_index，降级启发式检测",
                extra={
                    "run_id": run_id,
                    "pair_index": item.pair_index,
                    "candidate_pairs": len(pairs),
                },
            )
            return _heuristic_round(material)
        valid_indices.add(item.pair_index)

    conflicts: list[ConflictDict] = []
    for item in parsed.results:
        if not item.is_conflict:
            continue
        conflicts.append(_conflict_from_llm_result(item, material=material, pair=pairs[item.pair_index]))
    tokens_used = int(completion.usage.get("total_tokens", 0))
    return DetectionRound(conflicts, False, tokens_used)


# ---------------------------------------------------------------------------
# 裁决策略
# ---------------------------------------------------------------------------


def is_resolvable(conflict: ConflictDict) -> bool:
    """``low`` / ``medium`` 视为可自动解决；``high`` 留给用户裁决。"""
    return conflict.get("severity") in {"low", "medium"}


def resolve_by_authority(
    claims: list[ReportClaim],
    conflict: ConflictDict,
    material: Iterable[EvidenceDict],
) -> list[ReportClaim]:
    """按可信度裁决：保留 credibility 高的 evidence 对应 claim，丢弃另一条。

    规则：
    - 若 conflict 的两条 evidence credibility 不同，保留 rank 较小的对应 claim；
    - 平局时按 claim 出现顺序保留首条（M1 启发式约定）。
    - 若 evidence 不在 material 中 → 不动 claims。
    """
    ev_by_id: dict[str, EvidenceDict] = {ev["id"]: ev for ev in material}
    a = ev_by_id.get(conflict["evidence_a_id"])
    b = ev_by_id.get(conflict["evidence_b_id"])
    if a is None or b is None:
        return claims

    rank_a = _CREDIBILITY_RANK.get(a.get("credibility") or "D", 3)
    rank_b = _CREDIBILITY_RANK.get(b.get("credibility") or "D", 3)
    winner_ev_id = conflict["evidence_a_id"] if rank_a <= rank_b else conflict["evidence_b_id"]

    # 找到 winner_ev_id 对应的 claim（M1：claims 与 material 1:1 对齐）
    # 精确做法：每条 claim 的 citations[*].evidence_id 等于对应 evidence id。
    kept: list[ReportClaim] = []
    for c in claims:
        cite_ev_ids = {cit.get("evidence_id") for cit in (c.get("citations") or [])}
        if winner_ev_id in cite_ev_ids:
            kept.append(c)
    return kept


# ---------------------------------------------------------------------------
# 人工裁决四值收敛（M2-2 FR-11）
# ---------------------------------------------------------------------------

_STATUS_AWAITING_HUMAN: Literal["awaiting_human"] = "awaiting_human"
_STATUS_RESOLVED: Literal["resolved"] = "resolved"


def _citation_evidence_ids(claim: ReportClaim) -> set[str]:
    """取一条 claim 引用的全部 evidence_id。"""
    return {ev_id for cit in (claim.get("citations") or []) if (ev_id := cit.get("evidence_id")) is not None}


def apply_human_verdict(
    claims: list[ReportClaim],
    conflict: ConflictDict,
    choice: str,
) -> list[ReportClaim]:
    """按用户裁决 choice 四值收敛 claim 集合。

    - evidence_a：保留引用 a 的 claim，舍弃引用 b 的；
    - evidence_b：保留引用 b 的 claim，舍弃引用 a 的；
    - both：双方 claim 都保留，并在 claim 上打冲突 ID 并存标记
      （Reporter 据此显式呈现「观点并存」）；
    - reject：引用任一方的 claim 全部舍弃（不进入结论段）。

    与冲突双方无关的 claim 原样保留；未识别的 choice 不做任何剔除（保守不丢数据）。
    """
    ev_a = conflict["evidence_a_id"]
    ev_b = conflict["evidence_b_id"]

    if choice == "evidence_a":
        return [c for c in claims if ev_b not in _citation_evidence_ids(c)]
    if choice == "evidence_b":
        return [c for c in claims if ev_a not in _citation_evidence_ids(c)]
    if choice == "reject":
        return [c for c in claims if not (_citation_evidence_ids(c) & {ev_a, ev_b})]
    if choice == "both":
        for c in claims:
            if _citation_evidence_ids(c) & {ev_a, ev_b}:
                flags = list(c.get("divergence_flags") or [])
                if conflict["id"] not in flags:
                    flags.append(conflict["id"])
                c["divergence_flags"] = flags
        return claims
    return claims


def apply_pending_verdicts(
    claims: list[ReportClaim],
    conflicts: list[ConflictDict],
    verdicts: Iterable[VerdictDict],
) -> list[ReportClaim]:
    """回流入口：对仍处于 awaiting_human 的冲突应用裁决并就地置为 resolved。

    已是 resolved 的冲突（含自动收敛与已应用裁决）跳过，保证节点重入/多轮
    迭代时同一裁决只生效一次。返回收敛后的 claim 集合。
    """
    conflict_by_id = {c["id"]: c for c in conflicts}
    for verdict in verdicts:
        conflict = conflict_by_id.get(verdict["conflict_id"])
        if conflict is None or conflict.get("status") != _STATUS_AWAITING_HUMAN:
            continue
        claims = apply_human_verdict(claims, conflict, str(verdict.get("choice") or ""))
        conflict["status"] = _STATUS_RESOLVED
    return claims


def pending_conflict_ids(
    conflicts: Iterable[ConflictDict],
    verdicts: Iterable[VerdictDict],
) -> list[str]:
    """返回仍待人工裁决（awaiting_human 且无对应 verdict）的冲突 ID 列表。"""
    verdict_ids = {v["conflict_id"] for v in verdicts}
    return [
        c["id"] for c in conflicts if c.get("status") == _STATUS_AWAITING_HUMAN and c["id"] not in verdict_ids
    ]


async def _publish_conflict_detected(
    hub: RealtimeHub | None,
    *,
    run_id: str,
    conflict: ConflictDict,
) -> None:
    """逐条 high 冲突推送 conflict.detected（载荷嵌套于 payload，FR-5）。

    payload 取冲突实体子集（对齐前端 ConflictDetectedPayload）：详情与双方
    证据摘要由前端走 GET /conflicts/{id} 获取；hub 缺省或推送失败均不阻断主链路。
    """
    if hub is None:
        return
    event = {
        "type": "conflict.detected",
        "run_id": run_id,
        "stage": ResearchStage.CRITIQUE.value,
        "payload": {
            "id": conflict["id"],
            "claim": conflict.get("claim") or "",
            "evidence_a_id": conflict["evidence_a_id"],
            "evidence_b_id": conflict["evidence_b_id"],
            "type": conflict.get("type") or "factual",
            "severity": conflict.get("severity") or "high",
            "status": conflict.get("status") or _STATUS_AWAITING_HUMAN,
        },
    }
    try:
        await hub.publish(f"runs:{run_id}", event)
    except Exception as exc:  # noqa: BLE001 - 实时事件失败不阻断研究主链路
        log.warning(
            "conflict.detected 推送失败",
            extra={"run_id": run_id, "conflict_id": conflict["id"], "error": repr(exc)},
        )


async def _persist_detected_conflicts(
    session: AsyncSession | None,
    *,
    run_id: str,
    conflicts: list[ConflictDict],
) -> None:
    """新检出冲突幂等落库。

    与 Task 3 子问题/证据落库同口径：节点与执行器共享同一事务（见
    executor._drive_to_terminal），无 db_session（测试/未接线）时跳过；
    落库异常直接上抛由执行器标记 failed 并记录堆栈——落库失败不静默（FR-12）。
    """
    if session is None or not conflicts:
        return
    await persist_conflicts(session, run_id=run_id, items=conflicts)


# ---------------------------------------------------------------------------
# 节点入口
# ---------------------------------------------------------------------------


def _budget_exhausted(state: ResearchState, *, extra_tokens: int = 0) -> bool:
    """检查 token_used（含本节点已消耗）是否超过 budget × CRITIC_BUDGET_RATIO。"""
    used = int(state.get("token_used") or 0) + extra_tokens
    budget = int(state.get("token_budget") or 0)
    if budget <= 0:
        return False
    return used > budget * CRITIC_BUDGET_RATIO


@instrument(ResearchStage.CRITIQUE)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """审视节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中。

    M2-2 行为：注入 LLM 时走单批语义检测（8s 硬超时，失败保守降级），未注入
    时保持 M1 启发式路径；回流时先应用人工裁决四值收敛，再做新一轮检测；
    每条新冲突落库，high 冲突逐条推送 ``conflict.detected`` 并挂起。
    """
    material: list[EvidenceDict] = list(state.get("standardized_evidence") or [])
    claims: list[ReportClaim] = list(state.get("report_claims") or [])
    conflicts: list[ConflictDict] = list(state.get("conflicts") or [])
    verdicts: list[VerdictDict] = list(state.get("verdicts") or [])

    run_id = str(state.get("run_id") or "")
    llm = getattr(deps, "llm", None) if deps is not None else None
    db_session = getattr(deps, "db_session", None) if deps is not None else None
    hub = getattr(deps, "hub", None) if deps is not None else None
    degraded = False
    consumed_tokens = 0

    # 若 material 非空但还没派生过 claim（首次进入）→ 派生一次
    if not claims and material:
        claims = generate_draft_claims(material)

    # 回流入口：把已提交裁决应用到 claim 集合（四值收敛），冲突置 resolved
    claims = apply_pending_verdicts(claims, conflicts, verdicts)

    def _build_patch() -> dict[str, Any]:
        patch: dict[str, Any] = {
            "conflicts": conflicts,
            "verdicts": verdicts,
            "report_claims": claims,
            "critic_degraded": degraded,
            "token_used": int(state.get("token_used") or 0) + consumed_tokens,
        }
        pending_ids = pending_conflict_ids(conflicts, verdicts)
        if pending_ids:
            patch["interrupt_reason"] = "critique"
            patch["interrupt_payload"] = {"conflict_ids": pending_ids}
        return patch

    # 本跳新检出（非历史回流带入）的冲突：统一落库；high 的逐条推送在检出时发出
    detected: list[ConflictDict] = []

    # 跳出条件之一：material 不足两条或预算已耗尽 → 直接进入裁决/收敛出口
    if len(material) < 2 or _budget_exhausted(state):
        await _persist_detected_conflicts(db_session, run_id=run_id, conflicts=detected)
        return _build_patch()

    # 已处理过的证据对去重：迭代/回流重入时同一对不重复累积、不重复落库
    seen_pairs: set[frozenset[str]] = {frozenset({c["evidence_a_id"], c["evidence_b_id"]}) for c in conflicts}

    # 迭代：detect → resolve（可解）/ 累积（不可解）→ 三类收敛跳出
    iteration = 0
    while iteration < MAX_CRITIC_ITERATIONS:
        iteration += 1
        if llm is None:
            new_conflicts = detect_conflicts(claims, material)
        else:
            round_result = await detect_conflicts_semantic(
                material,
                llm=llm,
                run_id=run_id,
                exclude_pairs=seen_pairs,
            )
            new_conflicts = round_result.conflicts
            degraded = degraded or round_result.degraded
            consumed_tokens += round_result.tokens_used

        if not new_conflicts:
            break

        progressed = False
        for c in new_conflicts:
            pair_key = frozenset({c["evidence_a_id"], c["evidence_b_id"]})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            progressed = True
            # 全部新冲突都进入 conflicts 列表留痕（resolved 不渲染为待办），
            # 并作为 seen_pairs 幂等锚点防恢复重放重复落库
            if is_resolvable(c):
                c["status"] = _STATUS_RESOLVED
                claims = resolve_by_authority(claims, c, material)
            else:
                c["status"] = _STATUS_AWAITING_HUMAN
                await _publish_conflict_detected(hub, run_id=run_id, conflict=c)
            conflicts.append(c)
            detected.append(c)

        # 本轮全是已处理对 → 已收敛；预算触顶同样跳出（NFR-1，LLM 消耗计入）
        if not progressed or _budget_exhausted(state, extra_tokens=consumed_tokens):
            break

    await _persist_detected_conflicts(db_session, run_id=run_id, conflicts=detected)
    return _build_patch()


__all__ = [
    "run",
    "generate_draft_claims",
    "detect_conflicts",
    "detect_conflicts_semantic",
    "build_candidate_pairs",
    "is_resolvable",
    "resolve_by_authority",
    "apply_human_verdict",
    "apply_pending_verdicts",
    "pending_conflict_ids",
    "serialize_conflict",
    "DetectionRound",
    "MAX_CRITIC_ITERATIONS",
    "CRITIC_BUDGET_RATIO",
    "CONFLICT_SIMILARITY_THRESHOLD",
    "LLM_BATCH_TIMEOUT_SECONDS",
    "MAX_LLM_CANDIDATE_PAIRS",
    "LLM_CANDIDATE_SIMILARITY_THRESHOLD",
]
