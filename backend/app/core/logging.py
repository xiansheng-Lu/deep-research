"""结构化日志：基于 structlog，统一 JSON 输出，自动注入 trace_id / run_id。"""

import logging
import sys
from typing import Any

import structlog

from app.core.config import Settings
from app.core.context import current_request_context


def configure_logging(settings: Settings) -> None:
    """初始化日志配置，应在应用启动时调用一次。"""
    is_json = settings.log_json

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_request_context,
    ]
    if is_json:
        processors.append(structlog.processors.dict_tracebacks)
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # 同步三方库日志格式，避免重复输出
    logging.basicConfig(level=getattr(logging, settings.log_level), stream=sys.stdout, force=True)


def _inject_request_context(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """将请求上下文中的 trace_id / run_id / user_id 注入日志字段。"""
    ctx = current_request_context.get()
    if not ctx:
        return event_dict
    for key, value in ctx.items():
        if value is not None and key not in event_dict:
            event_dict[key] = value
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取绑定到指定名称的结构化 logger。"""
    return structlog.get_logger(name) if name else structlog.get_logger()