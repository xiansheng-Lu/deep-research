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
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.db.base import new_ulid
from app.observability.metrics import record_evidence_fetch
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
from app.retrieval.base import RetrievalRequest
from app.retrieval.client import RetrievalClient, get_default_client
from app.retrieval.dedup import fingerprint
from app.retrieval.page_metadata import fetch_page_metadata
from app.retrieval.ranker import blend_score, score_relevance

if TYPE_CHECKING:
    from app.realtime.hub import RealtimeHub

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
    hit_published_at: datetime | None,
    hit_fetched_at: datetime | None,
    sub_question_id: str,
    fingerprint_str: str,
    relevance_score: float,
) -> EvidenceDict:
    """把 ``RetrievalHit`` 序列化成 ``EvidenceDict`` 形态。

    retrieve 阶段只写中性占位（source_type=search / level=tertiary /
    credibility=C）；权威类型与分级由 standardizer 节点按域名规则落定。
    """
    url = hit_url or ""
    domain = _extract_domain(url)
    return {
        "id": new_ulid(),
        "sub_question_id": sub_question_id,
        "url": url,
        "domain": domain,
        "title": (hit_title or "").strip()[:512],
        "snippet": (hit_snippet or "").strip()[:500],
        # 占位值不伪装分类结果；standardizer 按 source_rules 重新判定
        "source_type": "search",
        "source_level": "tertiary",
        "credibility": "C",
        "fingerprint": fingerprint_str,
        "published_at": hit_published_at.isoformat() if hit_published_at else None,
        "fetched_at": (hit_fetched_at or datetime.now(tz=UTC)).isoformat(),
        "relevance_score": relevance_score,
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
    deduped_hits = []
    for hit in extracted:
        fp = fingerprint(hit)
        if fp in seen_fps:
            continue
        seen_fps.add(fp)
        deduped_hits.append((hit, fp))

    # M2-6 T3：对 provider 未给发布时间的命中，小预算抓取页面补采元数据；
    # 失败静默（published_at 保持 None），不影响子问题成功状态
    missing_date_urls = [hit.url for hit, _fp in deduped_hits if not hit.published_at and hit.url]
    page_meta: dict[str, Any] = {}
    if missing_date_urls:
        try:
            page_meta = await fetch_page_metadata(missing_date_urls)
        except Exception as exc:  # noqa: BLE001 - 补采整体异常也降级为空
            log.warning(
                "页面元数据补采异常，按无补采处理",
                extra={"run_id": run_id, "sub_question_id": subq_id, "error": repr(exc)},
            )
            page_meta = {}

    evidence: list[EvidenceDict] = []
    for hit, fp in deduped_hits:
        published_at = hit.published_at
        published_source = "provider" if published_at is not None else "null"
        site_name: str | None = None
        if published_at is None and hit.url:
            meta = page_meta.get(hit.url)
            if meta is not None and meta.published_at is not None:
                published_at = meta.published_at
                published_source = "page"
                site_name = meta.site_name
        # M2-6 T2：按子问题计算词面相关性并与 provider 分融合
        lexical = score_relevance(
            subq["question"],
            title=hit.title,
            snippet=hit.snippet,
            content=hit.content,
        )
        relevance = blend_score(hit.score, lexical)
        ev = _hit_to_evidence_dict(
            hit_url=hit.url,
            hit_title=hit.title,
            hit_snippet=hit.snippet,
            hit_published_at=published_at,
            hit_fetched_at=hit.fetched_at,
            sub_question_id=subq_id,
            fingerprint_str=fp,
            relevance_score=relevance,
        )
        # retrieve 阶段先留打分/日期来源留痕；分类依据由 standardizer 补写
        ev["metadata_"] = {
            "relevance": {
                "provider": round(float(hit.score or 0.0), 3),
                "lexical": lexical,
                "blended": relevance,
            },
            "published_at_source": published_source,
            **({"site_name": site_name} if site_name else {}),
        }
        evidence.append(ev)

    # M2-6 T2：子问题内部证据按相关性降序（standardizer 仍以可信度为首要序）
    evidence.sort(key=lambda e: float(e.get("relevance_score") or 0.0), reverse=True)

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


async def _publish_sub_question_event(
    hub: RealtimeHub | None,
    *,
    run_id: str,
    event_type: str,
    sq: SubQuestionDict,
) -> None:
    """推送子问题生命周期帧（retrieve 阶段）。

    - ``sub_question.started``：每层调度前，status=running；
    - ``sub_question.finished``：层结果落库后，带终态 status 与 evidence_count，
      succeeded/failed/evidence_short 均如实携带（M2-4 §7）。
    """
    if hub is None:
        return
    payload: dict[str, Any] = {
        "sub_question_id": sq["id"],
        "status": sq.get("status") or "pending",
    }
    if event_type == "sub_question.finished":
        payload["evidence_count"] = len(sq.get("evidence_ids") or [])
    if event_type == "sub_question.created":
        # M2-5：补查子问题的追问文本与依赖关系随创建帧下发（对齐前端帧类型）
        payload["question"] = sq.get("question")
        payload["depends_on"] = list(sq.get("depends_on") or [])
    event = {
        "type": event_type,
        "stage": ResearchStage.RETRIEVE.value,
        "payload": payload,
    }
    try:
        await hub.publish(f"runs:{run_id}", event)
    except Exception as exc:  # noqa: BLE001 - 实时事件失败不阻断主链路
        log.warning(
            "%s 推送失败",
            event_type,
            extra={"run_id": run_id, "sub_question_id": sq["id"], "error": repr(exc)},
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
    hub: RealtimeHub | None = getattr(deps, "hub", None) if deps is not None else None
    run_id = str(state.get("run_id") or "")
    if retrieval is None:
        log.warning("researcher_fan_out 未获取到检索客户端，全部子问题标记 failed")
        failed: list[SubQuestionDict] = [{**s, "status": "failed", "evidence_ids": []} for s in pending]
        by_id: dict[str, SubQuestionDict] = {s["id"]: s for s in subqs}
        by_id.update({f["id"]: f for f in failed})
        if db_session is not None:
            # 失败状态同样落库，保证看板/恢复链路可见终态
            await persist_sub_questions(db_session, run_id=run_id, items=list(by_id.values()))
        # 未实际调度：只发终态帧，前端看板据此把行收敛到 failed
        for f in failed:
            record_evidence_fetch(source_type="web", status="failed")
            await _publish_sub_question_event(hub, run_id=run_id, event_type="sub_question.finished", sq=f)
        return {"evidence": list(state.get("evidence") or []), "sub_questions": list(by_id.values())}

    top_k = top_k_for_tier(state.get("tier"))

    by_id = {s["id"]: s for s in subqs}
    all_evidence: list[EvidenceDict] = list(state.get("evidence") or [])

    # M2-5：拓扑分层改为动态队列——每层完成后在超步边界消费 ask_followup
    # 介入（追加补查层），followup 同样受 90% 成本闸门约束
    while True:
        active = [s for s in by_id.values() if s.get("status") not in _TERMINAL_STATUSES]
        if not active:
            break
        layers = _topological_layers(active)
        for layer in layers:
            # 调度前逐条发 started（status 固定 running，表示进入检索执行）
            for sq in layer:
                await _publish_sub_question_event(
                    hub,
                    run_id=run_id,
                    event_type="sub_question.started",
                    sq={**sq, "status": "running"},
                )
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
            # 落库后逐条发 finished（帧在提交前的毫秒级竞态由前端 REST 对齐补偿）
            for updated_sq in updated_sqs:
                record_evidence_fetch(source_type="web", status=str(updated_sq.get("status") or "failed"))
                await _publish_sub_question_event(
                    hub, run_id=run_id, event_type="sub_question.finished", sq=updated_sq
                )

        # 超步边界消费介入：返回新追加的 followup 子问题 id（空则结束循环）
        followup_ids = await _consume_interventions_at_boundary(
            db_session, hub=hub, run_id=run_id, by_id=by_id
        )
        if db_session is not None:
            # 用户剔除（含本超步内提交）：从 state 证据集移除，保证不进 standardize
            from app.services.interventions import excluded_evidence_ids

            excluded = await excluded_evidence_ids(db_session, run_id)
            if excluded:
                all_evidence = [e for e in all_evidence if e.get("id") not in excluded]
                for sq in by_id.values():
                    sq["evidence_ids"] = [
                        ev_id for ev_id in sq.get("evidence_ids", []) if ev_id not in excluded
                    ]
        if not followup_ids:
            break

    return {
        "evidence": all_evidence,
        "sub_questions": list(by_id.values()),
    }


async def _consume_interventions_at_boundary(
    db_session: Any,
    *,
    hub: RealtimeHub | None,
    run_id: str,
    by_id: dict[str, SubQuestionDict],
) -> list[str]:
    """fan-out 层边界消费 pending 的 ask_followup，返回新追加的子问题 id。

    exclude_evidence 在入队时已同步落 DB 标记，不进 pending 队列；state 侧
    剔除由调用方按 excluded_evidence_ids 统一处理。
    """
    if db_session is None:
        return []
    from app.services.interventions import mark_applied, pending_interventions

    pending_items = await pending_interventions(db_session, run_id)
    followup_ids: list[str] = []
    for item in pending_items:
        if item.type != "ask_followup":
            continue
        new_sq: SubQuestionDict = {
            "id": new_ulid(),
            "question": str(item.payload.get("question") or ""),
            "depends_on": [str(item.payload.get("sub_question_id") or "")],
            "status": "pending",
            "evidence_ids": [],
        }
        await persist_sub_questions(db_session, run_id=run_id, items=[new_sq])
        by_id[new_sq["id"]] = new_sq
        await _publish_sub_question_event(
            hub,
            run_id=run_id,
            event_type="sub_question.created",
            sq=new_sq,
        )
        await mark_applied(db_session, item.id)
        followup_ids.append(new_sq["id"])
    return followup_ids


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
