"""遥测域 Pydantic Schema：前端批量上报与指标聚合响应（M2-8a）。

与前端 ``TelemetryEvent``/``TelemetryBatchRequest`` 冻结形态对齐：
事件为 event/ts/run_id?/page?/props?，props 仅扁平标量。接收端点 204 无体；
指标端点返回聚合标量。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- 批与条校验常量（代码固化，不开放环境配置，契约 §14） ----
MAX_EVENTS_PER_BATCH = 200
MAX_BATCH_BYTES = 64 * 1024
MAX_EVENT_NAME_LEN = 128
MAX_PAGE_LEN = 128
MAX_RUN_ID_LEN = 64
MAX_PROPS_KEYS = 20
MAX_PROPS_KEY_LEN = 64
MAX_PROPS_STRING_LEN = 256
# 事件时间戳允许的时钟偏差（天）：前端断网补发窗口
EVENT_TS_SKEW_DAYS = 7
# 指标查询窗口
DEFAULT_METRICS_WINDOW_DAYS = 30
MAX_METRICS_WINDOW_DAYS = 90

# props 值允许的标量类型（None 视为缺省，不收 null 显式值）
TelemetryPropValue = str | int | float | bool


class TelemetryEventIn(BaseModel):
    """单个埋点事件入站的最小结构约束（批级 422 用）。

    只保证 event 为字符串、ts 为整数这两个不可恢复结构字段；run_id/page/props
    的类型/长度/取值域与 event 命名、ts 时间窗等条级规则由 service 逐行清洗
    （非法丢条、整批仍 204），避免一条非标量污染整批。
    """

    model_config = ConfigDict(extra="ignore")

    event: str
    ts: int
    run_id: Any = None
    page: Any = None
    props: Any = None


class TelemetryBatchRequest(BaseModel):
    """POST /telemetry/batch 请求体。"""

    events: list[TelemetryEventIn] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


# ---- 指标响应 ----


class MetricsWindow(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime

    model_config = ConfigDict(populate_by_name=True)


class RunMetrics(BaseModel):
    total_terminal: int
    succeeded: int
    failed: int
    cancelled: int
    # cancelled/paused 排除分母；无终态样本时为 None（不返回 0 误导）
    success_rate: float | None


class InterventionMetrics(BaseModel):
    runs_started: int
    runs_with_intervention: int
    intervention_rate: float | None
    by_action: dict[str, int]


class TraceabilityMetrics(BaseModel):
    final_reports: int
    # final 报告 citation_audit.numeric_claim_binding_rate 均值
    avg_numeric_binding_rate: float | None
    runs_with_citation_open: int
    citation_open_rate: float | None
    citation_open_events: int


class IntentMetrics(BaseModel):
    classify_total: int
    degraded: int
    degraded_rate: float | None


class IngestMetrics(BaseModel):
    # 进程内粗粒度计数，重启归零，仅趋势观察
    accepted_events: int
    dropped_events: int
    rate_limited_batches: int


class TelemetryMetricsResponse(BaseModel):
    window: MetricsWindow
    runs: RunMetrics
    intervention: InterventionMetrics
    traceability: TraceabilityMetrics
    intent: IntentMetrics
    ingest: IngestMetrics


# 供 service 内部使用的动作分类
InterveneAction = Literal["pause", "resume", "followup", "exclude", "clarify"]
