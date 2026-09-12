"""并行检索节点（§6.5.4 research_fan_out）。

契约：
- 输入：``ResearchState`` 含 ``sub_questions`` 与 ``tier``；``NodeDeps`` 可选含
    ``retrieval_client`` 与 ``db_session``。
- 输出：合并到 state 的字段包括
    ``evidence`` / ``sub_questions`` / ``current_stage`` / ``updated_at``。
- 行为：
    - 按 ``sub_questions`` 的 ``depends_on`` 做拓扑分层，逐层 ``asyncio.gather``
      并行检索；同一层内的子问题相互独立、不阻塞。
    - 档位 → 单子问题 top_k：quick=3 / standard=5 / deep=8 / extreme=10。
    - 失败隔离：任一子问题抛错不影响其它；失败子问题 ``status="failed"``、仍保留
      ``evidence_ids=[]``。
    - 跨子问题去重：按 ``app.retrieval.dedup.fingerprint`` 去重，避免同一 URL 在
      多子问题下重复落库。
    - 落库（可选，M2-2）：``deps.db_session`` 不为空时仅 upsert 子问题执行状态；
      证据统一在 standardize 节点完成分类后幂等写入，避免固化 retrieve 阶段的
      临时分级（见 ``app.orchestrator.persistence``）。
    - 重复执行：已终止状态（succeeded / failed / evidence_short）的子问题跳过，
      保持幂等。

占位签名（兼容 M0）：无 deps 时函数仍能运行；图骨架在 WP-6 接入 NodeDeps 后会切换为
``async def run(state, *, deps)`` 的统一形态。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.nodes._base import instrument
from app.orchestrator.persistence import persist_sub_questions
from app.orchestrator.state import (
    EvidenceDict,
    ResearchStage,
    ResearchState,
    SubQuestionDict,
)
from app.quota.tiers import Tier
from app.retrieval.base import RetrievalRequest, RetrievalSource
from app.retrieval.client import RetrievalClient, get_default_client
from app.retrieval.dedup import fingerprint

log = get_logger("orchestrator.researcher_fan_out")

# 档位 → 单子问题 top_k（§6.5.4 简化版：单轮 search + extract，不追问）
_TIER_TOP_K: dict[Tier, int] = {
    Tier.QUICK: 3,
    Tier.STANDARD: 5,
    Tier.DEEP: 8,
    Tier.EXTREME: 10,
}

# 子问题已终止状态：跳过重复检索
_TERMINAL_STATUSES = {"succeeded", "failed", "evidence_short"}

# 单子问题检索超时（秒），避免 Provider 卡住阻塞整层
_PER_SUB_QUESTION_TIMEOUT = 60.0


def top_k_for_tier(tier: str | None) -> int:
    """按档位返回单子问题检索条数上限；未知档位按 ``standard`` 处理。"""
    try:
        parsed = Tier(tier) if tier else Tier.STANDARD
    except ValueError:
        parsed = Tier.STANDARD
    return _TIER_TOP_K[parsed]


def _now_iso() -> str:
    """返回 UTC ISO 8601 时间戳。"""
    return datetime.now(tz=UTC).isoformat()


def _extract_domain(url: str | None) -> str:
    """从 URL 中抽出 host，并去掉 ``www.`` 前缀。"""
    if not url:
        return ""
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www.") and len(host) > 4:
        host = host[4:]
    return host


def _resolve_retrieval(deps: NodeDeps | None) -> RetrievalClient | None:
    """按 deps 优先级解析 ``RetrievalClient``。"""
    if deps is not None:
        client = getattr(deps, "retrieval_client", None)
        if client is not None:
            return client
    # 未注入检索客户端：回退到全局默认（可能因无 Provider 抛 ProviderUnavailableError）
    try:
        return get_default_client()
    except Exception as exc:  # noqa: BLE001 - 启动期异常收敛
        log.warning("researcher_fan_out 解析全局检索客户端失败: %r", exc)
        return None


def _topological_layers(subqs: list[SubQuestionDict]) -> list[list[SubQuestionDict]]:
    """按 ``depends_on`` 关系把子问题分层，每层内的子问题可并行执行。

    返回的每一层都是原 ``sub_question`` 的引用，不做字段修改；
    依赖链末端（无依赖）出现在第一层。
    """
    remaining: list[SubQuestionDict] = list(subqs)
    completed: set[str] = set()
    layers: list[list[SubQuestionDict]] = []

    while remaining:
        ready = [s for s in remaining if all(dep in completed for dep in s.get("depends_on") or [])]
        if not ready:
            # 防御性：存在环路或孤立依赖时，把剩余全部并入当层避免无限循环
            log.warning("researcher_fan_out 检测到非 DAG 依赖，整层兜底")
            ready = list(remaining)
        layers.append(ready)
        completed.update(s["id"] for s in ready)
        remaining = [s for s in remaining if s["id"] not in completed]

    return layers


def _hit_to_evidence_dict(
    *,
    hit_url: str | None,
    hit_title: str,
    hit_snippet: str,
    hit_source: RetrievalSource,
    hit_published_at: datetime | None,
    hit_fetched_at: datetime | None,
    sub_question_id: str,
    fingerprint_str: str,
) -> EvidenceDict:
    """把 ``RetrievalHit`` 序列化成 ``EvidenceDict`` 形态。"""
    url = hit_url or ""
    domain = _extract_domain(url)
    source_type = "news" if hit_source == RetrievalSource.WEB else "search"
    return {
        "id": new_ulid(),
        "sub_question_id": sub_question_id,
        "url": url,
        "domain": domain,
        "title": (hit_title or "").strip()[:512],
        "snippet": (hit_snippet or "").strip()[:500],
        # 标 source_type；source_level / credibility 由 standardizer 节点重新判定
        "source_type": source_type,
        "source_level": "tertiary",
        "credibility": "C",
        "fingerprint": fingerprint_str,
        "published_at": hit_published_at.isoformat() if hit_published_at else None,
        "fetched_at": (hit_fetched_at or datetime.now(tz=UTC)).isoformat(),
    }


async def _research_one(
    subq: SubQuestionDict,
    *,
    retrieval: RetrievalClient,
    run_id: str,
    top_k: int,
) -> tuple[SubQuestionDict, list[EvidenceDict]]:
    """处理单个子问题：search → extract → 去重 → 转 EvidenceDict。

    返回 ``(更新后的子问题, 证据列表)``。任何异常都会被捕获：子问题标
    ``status="failed"``，证据列表为空。
    """
    subq_id = subq["id"]
    try:
        req = RetrievalRequest(query=subq["question"], top_k=top_k)
        hits = await asyncio.wait_for(retrieval.search(req), timeout=_PER_SUB_QUESTION_TIMEOUT)
    except TimeoutError:
        log.warning(
            "researcher_fan_out 单子问题 search 超时",
            extra={"run_id": run_id, "sub_question_id": subq_id},
        )
        return ({**subq, "status": "failed", "evidence_ids": []}, [])
    except Exception as exc:  # noqa: BLE001 - 失败隔离
        log.warning(
            "researcher_fan_out 单子问题 search 失败",
            extra={"run_id": run_id, "sub_question_id": subq_id, "error": repr(exc)},
        )
        return ({**subq, "status": "failed", "evidence_ids": []}, [])

    if not hits:
        return (
            {**subq, "status": "evidence_short", "evidence_ids": []},
            [],
        )

    try:
        extracted = await asyncio.wait_for(retrieval.extract(hits), timeout=_PER_SUB_QUESTION_TIMEOUT)
    except TimeoutError:
        log.warning(
            "researcher_fan_out 单子问题 extract 超时",
            extra={"run_id": run_id, "sub_question_id": subq_id},
        )
        return ({**subq, "status": "evidence_short", "evidence_ids": []}, [])
    except Exception as exc:  # noqa: BLE001 - extract 失败降级
        log.warning(
            "researcher_fan_out 单子问题 extract 失败，降级为 evidence_short",
            extra={"run_id": run_id, "sub_question_id": subq_id, "error": repr(exc)},
        )
        return ({**subq, "status": "evidence_short", "evidence_ids": []}, [])

    # 单子问题内部仍按 fingerprint 去重
    seen_fps: set[str] = set()
    evidence: list[EvidenceDict] = []
    for hit in extracted:
        fp = fingerprint(hit)
        if fp in seen_fps:
            continue
        seen_fps.add(fp)
        evidence.append(
            _hit_to_evidence_dict(
                hit_url=hit.url,
                hit_title=hit.title,
                hit_snippet=hit.snippet,
                hit_source=hit.source,
                hit_published_at=hit.published_at,
                hit_fetched_at=hit.fetched_at,
                sub_question_id=subq_id,
                fingerprint_str=fp,
            )
        )

    if not evidence:
        return (
            {**subq, "status": "evidence_short", "evidence_ids": []},
            [],
        )
    return (
        {
            **subq,
            "status": "succeeded",
            "evidence_ids": [e["id"] for e in evidence],
        },
        evidence,
    )


@instrument(ResearchStage.RETRIEVE)
async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """检索节点入口。

    返回值会被 LangGraph 自动合并到 ``ResearchState`` 中。
    """
    subqs: list[SubQuestionDict] = list(state.get("sub_questions") or [])
    if not subqs:
        return {"evidence": list(state.get("evidence") or [])}

    # 幂等：仅对未终止子问题重新检索
    pending = [s for s in subqs if s.get("status") not in _TERMINAL_STATUSES]
    if not pending:
        return {"evidence": list(state.get("evidence") or [])}

    retrieval = _resolve_retrieval(deps)
    db_session = getattr(deps, "db_session", None) if deps is not None else None
    run_id = str(state.get("run_id") or "")
    if retrieval is None:
        log.warning("researcher_fan_out 未获取到检索客户端，全部子问题标记 failed")
        failed: list[SubQuestionDict] = [{**s, "status": "failed", "evidence_ids": []} for s in pending]
        by_id: dict[str, SubQuestionDict] = {s["id"]: s for s in subqs}
        by_id.update({f["id"]: f for f in failed})
        if db_session is not None:
            # 失败状态同样落库，保证看板/恢复链路可见终态
            await persist_sub_questions(db_session, run_id=run_id, items=list(by_id.values()))
        return {"evidence": list(state.get("evidence") or []), "sub_questions": list(by_id.values())}

    top_k = top_k_for_tier(state.get("tier"))

    layers = _topological_layers(pending)
    by_id = {s["id"]: s for s in subqs}
    all_evidence: list[EvidenceDict] = list(state.get("evidence") or [])

    for layer in layers:
        results = await asyncio.gather(
            *[_research_one(sq, retrieval=retrieval, run_id=run_id, top_k=top_k) for sq in layer],
            return_exceptions=False,
        )
        updated_sqs: list[SubQuestionDict] = []
        for updated_sq, evidence in results:
            by_id[updated_sq["id"]] = updated_sq
            updated_sqs.append(updated_sq)
            all_evidence.extend(evidence)
        if db_session is not None and updated_sqs:
            # 仅回写子问题执行状态；证据统一在 standardize 分类后落库
            await persist_sub_questions(db_session, run_id=run_id, items=updated_sqs)

    return {
        "evidence": all_evidence,
        "sub_questions": list(by_id.values()),
    }


__all__ = [
    "run",
    "top_k_for_tier",
    "topological_layers_for_sub_questions",
]


def topological_layers_for_sub_questions(
    subqs: list[SubQuestionDict],
) -> list[list[SubQuestionDict]]:
    """公开的拓扑分层辅助函数（供测试与上层编排复用）。"""
    return _topological_layers(subqs)
