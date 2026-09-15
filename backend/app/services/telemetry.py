"""埋点接收与指标聚合服务（M2-8a）。

接收：批/条两级校验、稳定采样、批量落库；指标：A8 三指标（成功率/介入率/
溯源率）+ 意图降级率，按时间窗在线聚合，全部业务指标只信业务表。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.audit import AuditEntry
from app.db.models.intervention import RunIntervention
from app.db.models.report import Report
from app.db.models.run import ResearchRun
from app.db.models.telemetry import TelemetryEvent
from app.schemas import telemetry as schema
from app.telemetry_ingest import (
    get_counters,
    get_rate_limiter,
    stable_sample,
)

log = get_logger("service.telemetry")

_EVENT_NAME_RE = re.compile(rf"^[a-z0-9_.]{{1,{schema.MAX_EVENT_NAME_LEN}}}$")
_PROPS_KEY_RE = re.compile(rf"^[a-z0-9_]{{1,{schema.MAX_PROPS_KEY_LEN}}}$")

# 介入动作 → 审计 action（pause/resume 业务表来源）
_PAUSE_ACTIONS = {"run.pause"}
_RESUME_ACTIONS = {"run.resume"}
# run_interventions.type → by_action 键
_INTERVENTION_ACTION_KEY = {
    "ask_followup": "followup",
    "exclude_evidence": "exclude",
}
_BY_ACTION_KEYS = ["pause", "resume", "followup", "exclude", "clarify"]


def _is_valid_scalar(value: Any) -> bool:
    """props 值必须是 string/int/float/bool；None/dict/list 拒绝（NaN/Inf 已被 JSON 层拒绝）。"""
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= schema.MAX_PROPS_STRING_LEN
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    return isinstance(value, float)


def _clean_event(raw: dict[str, Any], *, now: datetime) -> TelemetryEvent | None:
    """条级校验：返回 TelemetryEvent 或 None（非法条丢弃）。"""
    event = raw.get("event")
    if not isinstance(event, str) or not _EVENT_NAME_RE.fullmatch(event):
        return None

    ts = raw.get("ts")
    # bool 是 int 子类，需显式排除；负时间戳无意义
    if not isinstance(ts, int) or isinstance(ts, bool) or ts < 0:
        return None
    event_ts = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
    if abs((event_ts - now).total_seconds()) > schema.EVENT_TS_SKEW_DAYS * 86400:
        return None

    page = raw.get("page")
    if page is not None and (not isinstance(page, str) or len(page) > schema.MAX_PAGE_LEN):
        return None

    run_id = raw.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or len(run_id) > schema.MAX_RUN_ID_LEN):
        return None

    props_in = raw.get("props")
    props: dict[str, Any] = {}
    if props_in is not None:
        if not isinstance(props_in, dict) or len(props_in) > schema.MAX_PROPS_KEYS:
            return None
        for key, value in props_in.items():
            if not isinstance(key, str) or not _PROPS_KEY_RE.fullmatch(key):
                return None
            if value is None:
                # None 视为缺省，不写入
                continue
            if not _is_valid_scalar(value):
                return None
            props[key] = value

    return TelemetryEvent(
        event=event,
        run_id=run_id,
        page=page,
        props=props,
        event_ts=event_ts,
        created_at=now,
    )


async def ingest_batch(
    session: AsyncSession,
    *,
    user_id: str,
    team_id: str,
    events: list[dict[str, Any]],
) -> int:
    """校验 + 采样 + 清洗 + 批量落库；返回实际接受条数。

    调用方（路由）已完成批级结构校验与限流；本函数只负责条级清洗与采样。
    """
    settings = get_settings()
    if not stable_sample(user_id, settings.telemetry_sample_rate):
        # 未命中采样：整批静默丢弃，仍 204
        get_counters().add_dropped(len(events))
        return 0

    now = datetime.now(tz=UTC)
    rows: list[TelemetryEvent] = []
    dropped = 0
    for raw in events:
        row = _clean_event(raw, now=now)
        if row is None:
            dropped += 1
            continue
        # 归属永远由服务端注入
        row.user_id = user_id
        row.team_id = team_id
        rows.append(row)

    if rows:
        session.add_all(rows)
    get_counters().add_accepted(len(rows))
    if dropped:
        get_counters().add_dropped(dropped)
        log.info(
            "埋点批清洗丢弃非法条目",
            extra={"user_id": user_id, "dropped": dropped, "total": len(events)},
        )
    return len(rows)


def check_rate_limit(user_id: str) -> bool:
    """批频率限流：返回是否放行；超限累计一次限流计数。"""
    settings = get_settings()
    limiter = get_rate_limiter(settings.telemetry_rate_limit_per_min)
    allowed = limiter.allow(user_id)
    if not allowed:
        get_counters().add_rate_limited()
    return allowed


def parse_window(from_value: Any, to_value: Any) -> tuple[datetime, datetime]:
    now = datetime.now(tz=UTC)
    to = to_value if to_value else now
    frm = from_value if from_value else now - timedelta(days=schema.DEFAULT_METRICS_WINDOW_DAYS)
    if frm.tzinfo is None or to.tzinfo is None:
        raise ValueError("时间窗必须带时区")
    if frm >= to:
        raise ValueError("from 必须早于 to")
    if to - frm > timedelta(days=schema.MAX_METRICS_WINDOW_DAYS):
        raise ValueError("查询窗口不得超过 90 天")
    return frm, to


async def get_metrics(
    session: AsyncSession,
    *,
    frm: datetime,
    to: datetime,
) -> schema.TelemetryMetricsResponse:
    """按窗口聚合 A8 三指标与意图降级率（口径见方案 §5.5）。"""
    # ---- 成功率：按 created_at 落窗的终态 run（started 过） ----
    run_rows = (
        await session.scalars(
            select(ResearchRun).where(
                ResearchRun.created_at >= frm,
                ResearchRun.created_at < to,
                ResearchRun.started_at.is_not(None),
            )
        )
    ).all()
    succeeded = sum(1 for r in run_rows if r.status == "succeeded")
    failed = sum(1 for r in run_rows if r.status == "failed")
    cancelled = sum(1 for r in run_rows if r.status == "cancelled")
    terminal = succeeded + failed + cancelled
    success_rate = round(succeeded / (succeeded + failed), 3) if (succeeded + failed) else None

    # ---- 介入率：started run 为分母；业务表 distinct run 为分子 ----
    started_runs = {r.id for r in run_rows if r.started_at is not None}
    intervention_runs: set[str] = set()
    by_action = {key: 0 for key in _BY_ACTION_KEYS}

    audit_rows = (
        await session.scalars(
            select(AuditEntry).where(
                AuditEntry.target_type == "run",
                AuditEntry.created_at >= frm,
                AuditEntry.created_at < to,
                AuditEntry.action.in_(tuple(_PAUSE_ACTIONS | _RESUME_ACTIONS)),
            )
        )
    ).all()
    for entry in audit_rows:
        if entry.target_id not in started_runs:
            continue
        intervention_runs.add(entry.target_id)
        if entry.action in _PAUSE_ACTIONS:
            by_action["pause"] += 1
        elif entry.action in _RESUME_ACTIONS:
            by_action["resume"] += 1

    intervention_rows = (
        await session.scalars(
            select(RunIntervention).where(
                RunIntervention.status == "applied",
                RunIntervention.created_at >= frm,
                RunIntervention.created_at < to,
            )
        )
    ).all()
    for row in intervention_rows:
        key = _INTERVENTION_ACTION_KEY.get(row.type)
        if key is None:
            continue
        by_action[key] += 1
        if row.run_id in started_runs:
            intervention_runs.add(row.run_id)

    # clarify 取前端事件（观测口径，不参与权威分子）
    clarify_count = await _count_event_props(
        session,
        event="cockpit.intervene",
        frm=frm,
        to=to,
        match={"action": "clarify", "result": "success"},
    )
    by_action["clarify"] = clarify_count

    intervention_rate = round(len(intervention_runs) / len(started_runs), 3) if started_runs else None

    # ---- 溯源率：final 报告机器口径 + citation.open 交互口径 ----
    final_reports = (
        await session.scalars(
            select(Report).where(
                Report.status == "final",
                Report.created_at >= frm,
                Report.created_at < to,
            )
        )
    ).all()
    rates: list[float] = []
    for report in final_reports:
        payload = report.content_json if isinstance(report.content_json, dict) else {}
        audit = payload.get("citation_audit")
        if isinstance(audit, dict) and isinstance(audit.get("numeric_claim_binding_rate"), (int, float)):
            rates.append(float(audit["numeric_claim_binding_rate"]))
    avg_binding = round(sum(rates) / len(rates), 3) if rates else None

    final_run_ids = {r.run_id for r in final_reports}
    open_run_ids = await _distinct_run_ids_for_event(session, event="report.citation.open", frm=frm, to=to)
    open_runs_in_final = open_run_ids & final_run_ids
    open_event_count = await _count_events(session, event="report.citation.open", frm=frm, to=to)
    citation_open_rate = round(len(open_runs_in_final) / len(final_run_ids), 3) if final_run_ids else None

    # ---- 意图降级率（前端观测口径） ----
    intent_total = await _count_events(session, event="intent.classify", frm=frm, to=to)
    intent_degraded = await _count_event_props(
        session,
        event="intent.classify",
        frm=frm,
        to=to,
        match={"degraded": True},
    )
    intent_rate = round(intent_degraded / intent_total, 3) if intent_total else None

    counters = get_counters().snapshot()
    return schema.TelemetryMetricsResponse(
        window=schema.MetricsWindow.model_validate({"from": frm, "to": to}),
        runs=schema.RunMetrics(
            total_terminal=terminal,
            succeeded=succeeded,
            failed=failed,
            cancelled=cancelled,
            success_rate=success_rate,
        ),
        intervention=schema.InterventionMetrics(
            runs_started=len(started_runs),
            runs_with_intervention=len(intervention_runs),
            intervention_rate=intervention_rate,
            by_action=by_action,
        ),
        traceability=schema.TraceabilityMetrics(
            final_reports=len(final_reports),
            avg_numeric_binding_rate=avg_binding,
            runs_with_citation_open=len(open_runs_in_final),
            citation_open_rate=citation_open_rate,
            citation_open_events=open_event_count,
        ),
        intent=schema.IntentMetrics(
            classify_total=intent_total,
            degraded=intent_degraded,
            degraded_rate=intent_rate,
        ),
        ingest=schema.IngestMetrics(**counters),
    )


async def _count_events(session: AsyncSession, *, event: str, frm: datetime, to: datetime) -> int:
    return len(
        (
            await session.scalars(
                select(TelemetryEvent.id).where(
                    TelemetryEvent.event == event,
                    TelemetryEvent.created_at >= frm,
                    TelemetryEvent.created_at < to,
                )
            )
        ).all()
    )


async def _distinct_run_ids_for_event(
    session: AsyncSession, *, event: str, frm: datetime, to: datetime
) -> set[str]:
    rows = (
        await session.scalars(
            select(TelemetryEvent.run_id)
            .distinct()
            .where(
                TelemetryEvent.event == event,
                TelemetryEvent.run_id.is_not(None),
                TelemetryEvent.created_at >= frm,
                TelemetryEvent.created_at < to,
            )
        )
    ).all()
    return {rid for rid in rows if rid}


async def _count_event_props(
    session: AsyncSession,
    *,
    event: str,
    frm: datetime,
    to: datetime,
    match: dict[str, Any],
) -> int:
    """统计窗口内事件 props 全部命中 match 标量的条数（小数据量 Python 过滤）。"""
    rows = (
        await session.scalars(
            select(TelemetryEvent.props).where(
                TelemetryEvent.event == event,
                TelemetryEvent.created_at >= frm,
                TelemetryEvent.created_at < to,
            )
        )
    ).all()
    count = 0
    for props in rows:
        props = props if isinstance(props, dict) else {}
        if all(props.get(key) == value for key, value in match.items()):
            count += 1
    return count
