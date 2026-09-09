"""请求上下文：通过 ContextVar 在 async 链路中传递 trace_id / run_id / user_id。

日志与追踪中间件统一在此写入，下游业务代码可随时读取。
"""

from contextvars import ContextVar
from typing import Any

current_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_request_context", default=None
)


def bind_request_context(**values: Any) -> None:
    """向当前上下文注入键值（同名键会被覆盖）。"""
    ctx = dict(current_request_context.get() or {})
    ctx.update({k: v for k, v in values.items() if v is not None})
    current_request_context.set(ctx)


def clear_request_context() -> None:
    """清空当前请求上下文（用于测试或上下文切换）。"""
    current_request_context.set(None)


def get_request_value(key: str, default: Any = None) -> Any:
    """读取上下文中的单个值。"""
    ctx = current_request_context.get() or {}
    return ctx.get(key, default)