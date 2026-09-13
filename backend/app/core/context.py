"""请求上下文：通过 ContextVar 在 async 链路中传递 trace_id / run_id / user_id。

日志与追踪中间件统一在此写入，下游业务代码可随时读取。
M2-3 起 ``current_run_id`` / ``current_stage`` 同时作为 Prometheus LLM 指标
标签来源：执行器驱动期绑定，``LLMClient`` 打点时读取，缺失标 unknown。
"""

from contextvars import ContextVar
from typing import Any

current_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_request_context", default=None
)

#: 当前图执行的 run_id / 阶段（后台任务内绑定；非图上下文保持 None）
current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_stage: ContextVar[str | None] = ContextVar("current_stage", default=None)


def bind_request_context(**values: Any) -> None:
    """向当前上下文注入键值（同名键会被覆盖）。"""
    ctx = dict(current_request_context.get() or {})
    ctx.update({k: v for k, v in values.items() if v is not None})
    current_request_context.set(ctx)


def bind_run_context(*, run_id: str | None = None, stage: str | None = None) -> None:
    """绑定当前研究执行上下文（供指标打点读取）；传 None 不改写既有值。"""
    if run_id is not None:
        current_run_id.set(run_id)
    if stage is not None:
        current_stage.set(stage)


def clear_request_context() -> None:
    """清空当前请求上下文（用于测试或上下文切换）。"""
    current_request_context.set(None)


def get_request_value(key: str, default: Any = None) -> Any:
    """读取上下文中的单个值。"""
    ctx = current_request_context.get() or {}
    return ctx.get(key, default)
